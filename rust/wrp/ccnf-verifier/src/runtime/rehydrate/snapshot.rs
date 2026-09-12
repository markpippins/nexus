pub trait ReplaySnapshot {
    fn get(&self, key: &[u8]) -> Option<Vec<u8>>;
    fn scan(&self, prefix: &[u8]) -> Box<dyn Iterator<Item = (Vec<u8>, Vec<u8>)>>;
    fn height(&self) -> u64;
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::runtime::rehydrate::fixtures::MockSnapshot;

    #[test]
    fn test_get_returns_stored_value() {
        let snap = MockSnapshot::new([("v/alpha", "one"), ("v/beta", "two")]);
        assert_eq!(snap.get(b"v/alpha"), Some(b"one".to_vec()));
        assert_eq!(snap.get(b"v/beta"), Some(b"two".to_vec()));
    }

    #[test]
    fn test_get_missing_key_is_none() {
        let snap = MockSnapshot::new([("v/alpha", "one")]);
        assert_eq!(snap.get(b"v/zzz"), None);
    }

    #[test]
    fn test_scan_filters_by_prefix() {
        let snap = MockSnapshot::new([("v/alpha", "1"), ("w/beta", "2"), ("v/gamma", "3")]);
        let keys: Vec<Vec<u8>> = snap.scan(b"v/").map(|(k, _)| k).collect();
        // Sorted order: v/alpha before v/gamma; w/beta excluded.
        assert_eq!(keys, vec![b"v/alpha".to_vec(), b"v/gamma".to_vec()]);
    }

    #[test]
    fn test_scan_receives_the_caller_prefix() {
        let snap = MockSnapshot::new([("v/alpha", "1")]);
        let _ = snap.scan(b"v/");
        assert_eq!(snap.last_scan_prefix(), Some(b"v/".to_vec()));
    }

    #[test]
    fn test_height_reflects_snapshot_altitude() {
        let snap = MockSnapshot::new(Vec::<(&[u8], &[u8])>::new());
        assert_eq!(snap.height(), 7);
    }
}
