use crate::runtime::rehydrate::decode::Decoder;
use crate::runtime::rehydrate::view::View;

pub struct Route {
    pub prefix: &'static [u8],
    pub decoder: &'static dyn Decoder,
}

pub struct ViewRegistry {
    pub routes: &'static [Route],
}

impl ViewRegistry {
    pub fn decode(&self, key: &[u8], value: &[u8]) -> Option<Box<dyn View>> {
        for route in self.routes {
            if key.starts_with(route.prefix) {
                return route.decoder.decode(key, value);
            }
        }
        None
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::runtime::rehydrate::decode::Decoder;
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

    // &'static routes: the registry is constructed once at startup over a
    // static route table, exactly as production wiring does.
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

    fn registry() -> ViewRegistry {
        ViewRegistry { routes: ROUTES }
    }

    #[test]
    fn test_matching_prefix_decodes() {
        let v = registry()
            .decode(b"v/key1", b"value1")
            .expect("v/ must route to the pass decoder");
        assert_eq!(v.kind(), "example");
    }

    #[test]
    fn test_unrouted_prefix_returns_none() {
        assert!(registry().decode(b"x/key1", b"value1").is_none());
    }

    #[test]
    fn test_decoder_none_propagates_as_none() {
        // w/ routes to a decoder that rejects: the entry must be skipped,
        // not force-wrapped — registry defers to the decoder's verdict.
        assert!(registry().decode(b"w/key1", b"value1").is_none());
    }

    #[test]
    fn test_prefix_matching_is_exact_byte_prefix() {
        // 'vv/' starts with 'v' but not with 'v/' — must NOT route.
        assert!(registry().decode(b"vv/key1", b"value1").is_none());
    }
}
