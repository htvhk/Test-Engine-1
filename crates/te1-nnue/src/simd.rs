//! Architecture-specific NNUE kernels.
//!
//! Unsafe code is intentionally confined to this module. `Avx2Fma` is a
//! capability token that can only be constructed after runtime CPU detection.
//! Its safe methods validate tensor dimensions before entering the intrinsic
//! implementations, so the rest of TE1 does not need unsafe code.

#[derive(Debug, Clone, Copy)]
pub(crate) struct Avx2Fma {
    _private: (),
}

impl Avx2Fma {
    #[must_use]
    pub(crate) fn detect() -> Option<Self> {
        #[cfg(any(target_arch = "x86", target_arch = "x86_64"))]
        {
            if std::is_x86_feature_detected!("avx2") && std::is_x86_feature_detected!("fma") {
                Some(Self { _private: () })
            } else {
                None
            }
        }
        #[cfg(not(any(target_arch = "x86", target_arch = "x86_64")))]
        {
            None
        }
    }

    pub(crate) fn add_assign(self, dst: &mut [f32], src: &[f32]) -> bool {
        #[cfg(any(target_arch = "x86", target_arch = "x86_64"))]
        {
            if dst.len() != src.len() || !dst.len().is_multiple_of(x86_impl::LANES) {
                return false;
            }
            // SAFETY: this capability token is only constructed after AVX2+FMA
            // runtime detection. Equal slice lengths divisible by eight guarantee
            // that every unaligned 256-bit load/store stays within both slices.
            unsafe { x86_impl::add_assign_avx2(dst, src) }
            true
        }
        #[cfg(not(any(target_arch = "x86", target_arch = "x86_64")))]
        {
            let _ = (self, dst, src);
            false
        }
    }

    pub(crate) fn sub_assign(self, dst: &mut [f32], src: &[f32]) -> bool {
        #[cfg(any(target_arch = "x86", target_arch = "x86_64"))]
        {
            if dst.len() != src.len() || !dst.len().is_multiple_of(x86_impl::LANES) {
                return false;
            }
            // SAFETY: this capability token proves AVX2+FMA availability. The
            // validated equal, eight-divisible lengths keep all vector accesses
            // inside the slices.
            unsafe { x86_impl::sub_assign_avx2(dst, src) }
            true
        }
        #[cfg(not(any(target_arch = "x86", target_arch = "x86_64")))]
        {
            let _ = (self, dst, src);
            false
        }
    }

    pub(crate) fn hidden_crelu(
        self,
        first: &[f32],
        second: &[f32],
        width: usize,
        weights: &[f32],
        bias: &[f32],
        hidden: &mut [f32],
    ) -> bool {
        #[cfg(any(target_arch = "x86", target_arch = "x86_64"))]
        {
            let row_width = match width.checked_mul(2) {
                Some(value) => value,
                None => return false,
            };
            let expected_weights = match hidden.len().checked_mul(row_width) {
                Some(value) => value,
                None => return false,
            };
            if width == 0
                || !width.is_multiple_of(x86_impl::UNROLL)
                || first.len() != width
                || second.len() != width
                || bias.len() != hidden.len()
                || weights.len() != expected_weights
            {
                return false;
            }
            // SAFETY: the token proves AVX2+FMA CPU support. All tensor lengths
            // and row dimensions are validated above, width is a multiple of 16,
            // and the implementation uses only unaligned loads/stores within those
            // validated bounds.
            unsafe { x86_impl::hidden_crelu_avx2(first, second, width, weights, bias, hidden) }
            true
        }
        #[cfg(not(any(target_arch = "x86", target_arch = "x86_64")))]
        {
            let _ = (self, first, second, width, weights, bias, hidden);
            false
        }
    }
}

#[cfg(any(target_arch = "x86", target_arch = "x86_64"))]
mod x86_impl {
    #[cfg(target_arch = "x86")]
    use std::arch::x86::*;
    #[cfg(target_arch = "x86_64")]
    use std::arch::x86_64::*;

    pub(super) const LANES: usize = 8;
    pub(super) const UNROLL: usize = 16;

    /// Add `src` into `dst` using AVX2.
    ///
    /// # Safety
    /// Caller must prove AVX2+FMA CPU support, equal slice lengths, and that the
    /// common length is divisible by [`LANES`].
    #[target_feature(enable = "avx2,fma")]
    pub(super) unsafe fn add_assign_avx2(dst: &mut [f32], src: &[f32]) {
        let mut offset = 0usize;
        while offset < dst.len() {
            // SAFETY: the safe token method validated equal lengths divisible by
            // LANES, and this loop advances exactly one complete vector each step.
            let left = unsafe { _mm256_loadu_ps(dst.as_ptr().add(offset)) };
            // SAFETY: same validated chunk bounds apply to the source slice.
            let right = unsafe { _mm256_loadu_ps(src.as_ptr().add(offset)) };
            let sum = _mm256_add_ps(left, right);
            // SAFETY: destination pointer identifies the same validated chunk.
            unsafe { _mm256_storeu_ps(dst.as_mut_ptr().add(offset), sum) };
            offset += LANES;
        }
    }

    /// Subtract `src` from `dst` using AVX2.
    ///
    /// # Safety
    /// Caller must prove AVX2+FMA CPU support, equal slice lengths, and that the
    /// common length is divisible by [`LANES`].
    #[target_feature(enable = "avx2,fma")]
    pub(super) unsafe fn sub_assign_avx2(dst: &mut [f32], src: &[f32]) {
        let mut offset = 0usize;
        while offset < dst.len() {
            // SAFETY: the safe token method validated equal lengths divisible by
            // LANES, and this loop advances exactly one complete vector each step.
            let left = unsafe { _mm256_loadu_ps(dst.as_ptr().add(offset)) };
            // SAFETY: same validated chunk bounds apply to the source slice.
            let right = unsafe { _mm256_loadu_ps(src.as_ptr().add(offset)) };
            let difference = _mm256_sub_ps(left, right);
            // SAFETY: destination pointer identifies the same validated chunk.
            unsafe { _mm256_storeu_ps(dst.as_mut_ptr().add(offset), difference) };
            offset += LANES;
        }
    }

    /// Compute the fused two-perspective CReLU hidden transform with AVX2/FMA.
    ///
    /// # Safety
    /// Caller must prove AVX2+FMA CPU support and validate all tensor dimensions:
    /// both accumulators must have `width` elements, `width` must be divisible by
    /// [`UNROLL`], `bias.len() == hidden.len()`, and `weights.len()` must equal
    /// `hidden.len() * 2 * width`.
    #[target_feature(enable = "avx2,fma")]
    pub(super) unsafe fn hidden_crelu_avx2(
        first: &[f32],
        second: &[f32],
        width: usize,
        weights: &[f32],
        bias: &[f32],
        hidden: &mut [f32],
    ) {
        let zero = _mm256_setzero_ps();
        let one = _mm256_set1_ps(1.0);
        let row_width = 2 * width;
        let mut row = 0usize;

        while row < hidden.len() {
            let row_start = row * row_width;
            let second_start = row_start + width;
            let mut first_a = _mm256_setzero_ps();
            let mut first_b = _mm256_setzero_ps();
            let mut second_a = _mm256_setzero_ps();
            let mut second_b = _mm256_setzero_ps();
            let mut offset = 0usize;

            while offset < width {
                // SAFETY: width is validated as a multiple of 16 and both input
                // slices have exactly `width` elements, so each 8-float chunk is in-bounds.
                let first_input_a = unsafe { _mm256_loadu_ps(first.as_ptr().add(offset)) };
                // SAFETY: offset + 8 is the second half of the validated 16-float chunk.
                let first_input_b = unsafe { _mm256_loadu_ps(first.as_ptr().add(offset + LANES)) };
                let first_active_a = _mm256_max_ps(zero, _mm256_min_ps(one, first_input_a));
                let first_active_b = _mm256_max_ps(zero, _mm256_min_ps(one, first_input_b));

                // SAFETY: weights length was validated as hidden.len() * 2*width;
                // these offsets stay inside this row's first-perspective weights.
                let first_weight_a =
                    unsafe { _mm256_loadu_ps(weights.as_ptr().add(row_start + offset)) };
                // SAFETY: same validated row, second vector of the current chunk.
                let first_weight_b =
                    unsafe { _mm256_loadu_ps(weights.as_ptr().add(row_start + offset + LANES)) };
                first_a = _mm256_fmadd_ps(first_weight_a, first_active_a, first_a);
                first_b = _mm256_fmadd_ps(first_weight_b, first_active_b, first_b);

                // SAFETY: second input slice has the same validated width.
                let second_input_a = unsafe { _mm256_loadu_ps(second.as_ptr().add(offset)) };
                // SAFETY: offset + 8 remains within the current validated chunk.
                let second_input_b =
                    unsafe { _mm256_loadu_ps(second.as_ptr().add(offset + LANES)) };
                let second_active_a = _mm256_max_ps(zero, _mm256_min_ps(one, second_input_a));
                let second_active_b = _mm256_max_ps(zero, _mm256_min_ps(one, second_input_b));

                // SAFETY: second_start is the validated second-perspective section
                // of the current hidden row.
                let second_weight_a =
                    unsafe { _mm256_loadu_ps(weights.as_ptr().add(second_start + offset)) };
                // SAFETY: same validated second-perspective section and chunk.
                let second_weight_b =
                    unsafe { _mm256_loadu_ps(weights.as_ptr().add(second_start + offset + LANES)) };
                second_a = _mm256_fmadd_ps(second_weight_a, second_active_a, second_a);
                second_b = _mm256_fmadd_ps(second_weight_b, second_active_b, second_b);
                offset += UNROLL;
            }

            // Collapse the four independent dependency chains while values are
            // still in registers. This keeps the inner loop highly parallel and
            // reduces the horizontal-reduction spill from 32 floats to eight.
            let first_sum = _mm256_add_ps(first_a, first_b);
            let second_sum = _mm256_add_ps(second_a, second_b);
            let total = _mm256_add_ps(first_sum, second_sum);
            let mut lanes = [0.0f32; LANES];
            // SAFETY: the destination array contains exactly eight f32 values,
            // matching one 256-bit vector store.
            unsafe { _mm256_storeu_ps(lanes.as_mut_ptr(), total) };

            let mut value = bias[row];
            for lane in lanes {
                value += lane;
            }
            hidden[row] = value.clamp(0.0, 1.0);
            row += 1;
        }
    }
}
