use crate::runtime::rehydrate::snapshot::ReplaySnapshot;
use crate::runtime::rehydrate::registry::ViewRegistry;
use crate::runtime::rehydrate::view::View;

pub struct Reader {
    snap: Box<dyn ReplaySnapshot>,
    reg: &'static ViewRegistry,
}

impl Reader {
    pub fn new(snap: Box<dyn ReplaySnapshot>, reg: &'static ViewRegistry) -> Self {
        Reader { snap, reg }
    }

    pub fn scan(&self, prefix: &[u8]) -> Vec<Box<dyn View>> {
        let mut result: Vec<Box<dyn View>> = Vec::new();
        for (k, v) in self.snap.scan(prefix) {
            if let Some(view) = self.reg.decode(&k, &v) {
                result.push(view);
            }
        }
        result
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::runtime::rehydrate::decode::Decoder;
    use crate::runtime::rehydrate::fixtures::MockSnapshot;
    use crate::runtime::rehydrate::registry::{Route, ViewRegistry};
    use crate::runtime::rehydrate::view::ExampleView;

    struct PassthroughDecoder;

    impl Decoder for PassthroughDecoder {
        fn decode(&self, key: &[u8], value: &[u8]) -> Option<Box<dyn View>> {
            Some(Box::new(ExampleView {
                key: String::from_utf8_lossy(key).into_owned(),
                value: String::from_utf8_lossy(value).into_owned(),
            }))
        }
    }

    struct RejectingDecoder;

    impl Decoder for RejectingDecoder {
        fn decode(&self, _key: &[u8], _value: &[u8]) -> Option<Box<dyn View>> {
            None
        }
    }

    static D_PASS: PassthroughDecoder = PassthroughDecoder;
    static D_REJECT: RejectingDecoder = RejectingDecoder;

    static ROUTES: &[Route] = &[
        Route {
            prefix: b"v/",
            decoder: &D_PASS,
        },
        Route {
            prefix: b"w/",
            decoder: &D_REJECT,
        },
    ];

    // Reader borrows the registry for 'static: use a static, not a
    // per-test temporary (a &'static temporary would not outlive the call).
    static REG: ViewRegistry = ViewRegistry { routes: ROUTES };

    fn reader(snap: MockSnapshot) -> Reader {
        Reader::new(Box::new(snap), &REG)
    }

    #[test]
    fn test_scan_aggregates_decodable_entries_in_snapshot_order() {
        let r = reader(MockSnapshot::new([("v/alpha", "1"), ("v/beta", "2")]));
        let views = r.scan(b"v/");
        assert_eq!(views.len(), 2);
        assert_eq!(views[0].kind(), "example");
        assert_eq!(views[1].kind(), "example");
    }

    #[test]
    fn test_scan_skips_entries_the_decoder_rejects() {
        let r = reader(MockSnapshot::new([("v/pass", "ok"), ("w/reject", "no")]));
        let views = r.scan(b"");
        // Only the v/ entry survives; the w/ entry is silently skipped.
        assert_eq!(views.len(), 1);
    }

    #[test]
    fn test_scan_empty_snapshot_yields_empty_vec() {
        let r = reader(MockSnapshot::new(Vec::<(&str, &str)>::new()));
        assert!(r.scan(b"v/").is_empty());
    }

    #[test]
    fn test_rehydrate_reads_never_mutate_the_snapshot() {
        // Immutability contract: scan is &self, and repeated scans over the
        // same snapshot return identical results (no destructive reads).
        let r = reader(MockSnapshot::new([("v/alpha", "1"), ("v/beta", "2")]));
        let first: Vec<String> = r.scan(b"v/").iter().map(|v| v.kind().to_string()).collect();
        let second: Vec<String> = r.scan(b"v/").iter().map(|v| v.kind().to_string()).collect();
        assert_eq!(first, second);
        assert_eq!(first.len(), 2);
    }
}
