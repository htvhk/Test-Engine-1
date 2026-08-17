#![forbid(unsafe_code)]

#[cfg(not(target_has_atomic = "64"))]
compile_error!("te1-tt requires native 64-bit atomic compare-and-swap support");

use std::hint::spin_loop;
use std::sync::atomic::{AtomicU8, AtomicU64, Ordering};
use te1_chess::PackedMove;

const SCORE_SHIFT: u32 = 16;
const DEPTH_SHIFT: u32 = 32;
const BOUND_SHIFT: u32 = 40;
const GENERATION_SHIFT: u32 = 42;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Bound {
    Exact,
    Lower,
    Upper,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Entry {
    pub depth: i16,
    pub score: i32,
    pub bound: Bound,
    pub best_move: PackedMove,
    pub generation: u8,
}

#[derive(Debug)]
struct Slot {
    guard: AtomicU8,
    key_xor_data: AtomicU64,
    data: AtomicU64,
}

struct SlotGuard<'a> {
    guard: &'a AtomicU8,
}

impl Drop for SlotGuard<'_> {
    fn drop(&mut self) {
        self.guard.store(0, Ordering::Release);
    }
}

impl Slot {
    fn empty() -> Self {
        Self {
            guard: AtomicU8::new(0),
            key_xor_data: AtomicU64::new(0),
            data: AtomicU64::new(0),
        }
    }

    fn lock(&self) -> SlotGuard<'_> {
        let mut backoff: u32 = 1;
        loop {
            if self
                .guard
                .compare_exchange_weak(0, 1, Ordering::Acquire, Ordering::Relaxed)
                .is_ok()
            {
                return SlotGuard { guard: &self.guard };
            }
            for _ in 0..backoff {
                spin_loop();
            }
            backoff = (backoff * 2).min(64);
            if backoff == 64 {
                std::thread::yield_now();
            }
        }
    }

    fn clear(&self) {
        let _guard = self.lock();
        self.key_xor_data.store(0, Ordering::Relaxed);
        self.data.store(0, Ordering::Relaxed);
    }

    fn read(&self) -> Option<(u64, Entry)> {
        let _guard = self.lock();
        let key_xor_data = self.key_xor_data.load(Ordering::Relaxed);
        let data = self.data.load(Ordering::Relaxed);
        let entry = unpack(data)?;
        Some((key_xor_data ^ data, entry))
    }

    fn store_if_replaceable(&self, key: u64, entry: Entry) {
        let _guard = self.lock();
        let current_data = self.data.load(Ordering::Relaxed);
        let current_key_xor_data = self.key_xor_data.load(Ordering::Relaxed);
        let replace = match unpack(current_data) {
            None => true,
            Some(current) => {
                let stored_key = current_key_xor_data ^ current_data;
                let same_position = stored_key == key;
                let fresh = current.generation == entry.generation;
                !same_position
                    || !fresh
                    || entry.depth >= current.depth.saturating_sub(2)
                    || entry.bound == Bound::Exact
            }
        };
        if replace {
            let data = pack(entry);
            self.data.store(data, Ordering::Relaxed);
            self.key_xor_data.store(key ^ data, Ordering::Relaxed);
        }
    }
}

#[derive(Debug)]
pub struct TranspositionTable {
    entries: Box<[Slot]>,
    mask: usize,
    generation: AtomicU8,
}

impl TranspositionTable {
    #[must_use]
    pub fn with_megabytes(megabytes: usize) -> Self {
        let bytes = megabytes.max(1).saturating_mul(1024 * 1024);
        let requested = (bytes / std::mem::size_of::<Slot>()).max(1);
        let count = floor_power_of_two(requested);
        let entries = (0..count)
            .map(|_| Slot::empty())
            .collect::<Vec<_>>()
            .into_boxed_slice();
        Self {
            entries,
            mask: count - 1,
            generation: AtomicU8::new(0),
        }
    }

    #[must_use]
    pub fn len(&self) -> usize {
        self.entries.len()
    }

    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }

    pub fn clear(&self) {
        for entry in &self.entries {
            entry.clear();
        }
        self.generation.fetch_add(1, Ordering::Relaxed);
    }

    pub fn new_search(&self) {
        self.generation.fetch_add(1, Ordering::Relaxed);
    }

    #[must_use]
    pub fn generation(&self) -> u8 {
        self.generation.load(Ordering::Relaxed)
    }

    #[must_use]
    pub fn probe(&self, key: u64) -> Option<Entry> {
        let (stored_key, entry) = self.entries[self.index(key)].read()?;
        (stored_key == key).then_some(entry)
    }

    pub fn store(&self, key: u64, depth: i16, score: i32, bound: Bound, best_move: PackedMove) {
        let entry = Entry {
            depth,
            score,
            bound,
            best_move,
            generation: self.generation(),
        };
        self.entries[self.index(key)].store_if_replaceable(key, entry);
    }

    #[must_use]
    pub fn hashfull_per_mille(&self) -> u16 {
        let generation = self.generation();
        let sample = self.entries.len().min(1_000);
        if sample == 0 {
            return 0;
        }
        let occupied = self.entries[..sample]
            .iter()
            .filter(|slot| {
                slot.read()
                    .is_some_and(|(_, entry)| entry.generation == generation)
            })
            .count();
        u16::try_from(occupied.saturating_mul(1_000) / sample).unwrap_or(1_000)
    }

    fn index(&self, key: u64) -> usize {
        (key as usize) & self.mask
    }
}

fn floor_power_of_two(value: usize) -> usize {
    debug_assert!(value > 0);
    1usize << (usize::BITS - 1 - value.leading_zeros())
}

fn bound_code(bound: Bound) -> u64 {
    match bound {
        Bound::Exact => 1,
        Bound::Lower => 2,
        Bound::Upper => 3,
    }
}

fn pack(entry: Entry) -> u64 {
    let score = entry.score.clamp(i32::from(i16::MIN), i32::from(i16::MAX));
    let score_i16 = i16::try_from(score).unwrap_or_default();
    let score_bits = u64::from(u16::from_ne_bytes(score_i16.to_ne_bytes()));
    let depth = u64::from(u8::try_from(entry.depth.clamp(0, 255)).unwrap_or_default());
    u64::from(entry.best_move.raw())
        | (score_bits << SCORE_SHIFT)
        | (depth << DEPTH_SHIFT)
        | (bound_code(entry.bound) << BOUND_SHIFT)
        | (u64::from(entry.generation) << GENERATION_SHIFT)
}

fn unpack(raw: u64) -> Option<Entry> {
    if raw == 0 {
        return None;
    }
    let move_raw = u16::try_from(raw & 0xffff).ok()?;
    let score_raw = u16::try_from((raw >> SCORE_SHIFT) & 0xffff).ok()?;
    let score = i32::from(i16::from_ne_bytes(score_raw.to_ne_bytes()));
    let depth = i16::from(u8::try_from((raw >> DEPTH_SHIFT) & 0xff).ok()?);
    let bound = match (raw >> BOUND_SHIFT) & 0x3 {
        1 => Bound::Exact,
        2 => Bound::Lower,
        3 => Bound::Upper,
        _ => return None,
    };
    let generation = u8::try_from((raw >> GENERATION_SHIFT) & 0xff).ok()?;
    Some(Entry {
        depth,
        score,
        bound,
        best_move: PackedMove::from_raw(move_raw),
        generation,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Arc;
    use std::thread;

    #[test]
    fn stores_and_probes_exact_key() {
        let table = TranspositionTable::with_megabytes(1);
        let best_move = PackedMove::from_raw(0x1234);
        table.store(42, 5, 123, Bound::Exact, best_move);
        let entry = table.probe(42).unwrap();
        assert_eq!(entry.depth, 5);
        assert_eq!(entry.score, 123);
        assert_eq!(entry.best_move, best_move);
        assert!(table.probe(43).is_none());
    }

    #[test]
    fn deeper_entry_is_not_replaced_by_shallower_non_exact_entry() {
        let table = TranspositionTable::with_megabytes(1);
        table.store(7, 8, 10, Bound::Lower, PackedMove::NONE);
        table.store(7, 3, 20, Bound::Upper, PackedMove::NONE);
        assert_eq!(table.probe(7).unwrap().depth, 8);
    }

    #[test]
    fn clear_removes_entries() {
        let table = TranspositionTable::with_megabytes(1);
        table.store(9, 1, 1, Bound::Exact, PackedMove::NONE);
        table.clear();
        assert!(table.probe(9).is_none());
    }

    #[test]
    fn concurrent_access_is_safe_and_retains_thread_entries() {
        let table = Arc::new(TranspositionTable::with_megabytes(1));
        thread::scope(|scope| {
            let mut handles = Vec::new();
            for thread_id in 1u64..=8 {
                let table = Arc::clone(&table);
                handles.push(scope.spawn(move || {
                    let mv = PackedMove::from_raw(u16::try_from(thread_id).unwrap());
                    table.store(
                        thread_id,
                        6,
                        i32::try_from(thread_id).unwrap(),
                        Bound::Exact,
                        mv,
                    );
                }));
            }
            for handle in handles {
                handle.join().unwrap();
            }
        });
        for thread_id in 1u64..=8 {
            let entry = table.probe(thread_id).unwrap();
            assert_eq!(entry.score, i32::try_from(thread_id).unwrap());
        }
    }

    #[test]
    fn colliding_slot_concurrency_never_returns_an_unrelated_exact_key() {
        let table = Arc::new(TranspositionTable::with_megabytes(1));
        let stride = u64::try_from(table.len()).unwrap();
        thread::scope(|scope| {
            let mut handles = Vec::new();
            for writer_id in 0u64..4 {
                let table = Arc::clone(&table);
                handles.push(scope.spawn(move || {
                    let key = 17 + writer_id * stride;
                    let mv = PackedMove::from_raw(u16::try_from(writer_id + 1).unwrap());
                    for iteration in 0..20_000i32 {
                        table.store(key, 8, iteration, Bound::Exact, mv);
                    }
                }));
            }
            let table = Arc::clone(&table);
            handles.push(scope.spawn(move || {
                for _ in 0..100_000 {
                    for writer_id in 0u64..4 {
                        let key = 17 + writer_id * stride;
                        if let Some(entry) = table.probe(key) {
                            assert_eq!(
                                entry.best_move,
                                PackedMove::from_raw(u16::try_from(writer_id + 1).unwrap())
                            );
                        }
                    }
                }
            }));
            for handle in handles {
                handle.join().unwrap();
            }
        });
    }

    #[test]
    fn table_size_is_a_nonzero_power_of_two() {
        let table = TranspositionTable::with_megabytes(3);
        assert!(table.len().is_power_of_two());
        assert!(!table.is_empty());
    }
}
