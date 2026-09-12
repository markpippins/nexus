use crate::runtime::rehydrate::view::View;
use crate::runtime::rehydrate::registry::ViewRegistry;

pub struct ScanIterator {
    // Placeholder for scan/iteration logic
}

pub fn scan(_prefix: &[u8], _reg: &ViewRegistry) -> Vec<Box<dyn View>> {
    Vec::new()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::runtime::rehydrate::decode::Decoder;
    use crate::runtime::rehydrate::registry::Route;
    use crate::runtime::rehydrate::view::ExampleView;

    struct PassDecoder;

    impl Decoder for PassDecoder {
        fn decode(&self, key: &[u8], value: &[u8]) -> Option<Box<dyn View>> {
            Some(Box::new(ExampleView {
                key: String::from_utf8_lossy(key).into_owned(),
                value: String::from_utf8_lossy(value).into_owned(),
            }))
        }
    }

    static D: PassDecoder = PassDecoder;

    static ROUTES: &[Route] = &[Route {
        prefix: b"v/",
        decoder: &D,
    }];

    #[test]
    fn test_module_scan_is_a_placeholder_returning_empty() {
        // Pins the CURRENT contract of the free-function scan: it ignores
        // its arguments and yields no views (Reader::scan is the real
        // implementation). When real iteration lands here, this test fails
        // the diff on purpose — the change then needs its own real spec
        // instead of silently replacing the placeholder.
        let views = scan(b"v/", &ViewRegistry { routes: ROUTES });
        assert!(
            views.is_empty(),
            "placeholder scan must stay empty until properly implemented"
        );
    }
}
