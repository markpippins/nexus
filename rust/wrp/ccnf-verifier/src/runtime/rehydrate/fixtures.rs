//! Test fixtures for the rehydrate layer (compiled only under `cfg(test)`).
//!
//! `MockSnapshot` is the reference `ReplaySnapshot` implementation: sorted
//! keys (BTreeMap), strict prefix filtering, and a recorded last-scan
//! prefix so tests can assert forwarding behavior.

use crate::runtime::rehydrate::snapshot::ReplaySnapshot;
use std::collections::BTreeMap;

pub(crate) struct MockSnapshot {
    map: BTreeMap<Vec<u8>, Vec<u8>>,
    last_scan_prefix: std::sync::Mutex<Option<Vec<u8>>>,
    height: u64,
}

impl MockSnapshot {
    /// Build from (key, value) pairs. Takes `IntoIterator` so callers can
    /// pass heterogeneous `&[(&[u8], &[u8])]` literals without size
    /// unification errors.
    pub(crate) fn new<I, K, V>(entries: I) -> Self
    where
        I: IntoIterator<Item = (K, V)>,
        K: AsRef<[u8]>,
        V: AsRef<[u8]>,
    {
        let mut map = BTreeMap::new();
        for (k, v) in entries {
            map.insert(k.as_ref().to_vec(), v.as_ref().to_vec());
        }
        MockSnapshot {
            map,
            last_scan_prefix: std::sync::Mutex::new(None),
            height: 7,
        }
    }

    pub(crate) fn last_scan_prefix(&self) -> Option<Vec<u8>> {
        self.last_scan_prefix.lock().unwrap().clone()
    }
}

impl ReplaySnapshot for MockSnapshot {
    fn get(&self, key: &[u8]) -> Option<Vec<u8>> {
        self.map.get(key).cloned()
    }

    fn scan(&self, prefix: &[u8]) -> Box<dyn Iterator<Item = (Vec<u8>, Vec<u8>)>> {
        *self.last_scan_prefix.lock().unwrap() = Some(prefix.to_vec());
        let hits: Vec<(Vec<u8>, Vec<u8>)> = self
            .map
            .range(prefix.to_vec()..)
            .filter(|(k, _)| k.starts_with(prefix))
            .map(|(k, v)| (k.clone(), v.clone()))
            .collect();
        Box::new(hits.into_iter())
    }

    fn height(&self) -> u64 {
        self.height
    }
}
