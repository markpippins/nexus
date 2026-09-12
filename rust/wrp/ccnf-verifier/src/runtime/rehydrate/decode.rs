use crate::runtime::rehydrate::view::View;

// Sync bound: ViewRegistry routes are `&'static dyn Decoder` (a startup-time
// static route table), and statics require their referents to be Sync.
pub trait Decoder: Sync {
    fn decode(&self, key: &[u8], value: &[u8]) -> Option<Box<dyn View>>;
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::runtime::rehydrate::view::ExampleView;

    /// Turns "key<separator>value" strings into ExampleViews; returns None
    /// on values that are not valid UTF-8 (the skip-malformed contract).
    struct ExampleDecoder {
        separator: u8,
    }

    impl ExampleDecoder {
        fn new(separator: u8) -> Self {
            ExampleDecoder { separator }
        }
    }

    impl Decoder for ExampleDecoder {
        fn decode(&self, key: &[u8], value: &[u8]) -> Option<Box<dyn View>> {
            let key_str = std::str::from_utf8(key).ok()?;
            let value_str = std::str::from_utf8(value).ok()?;
            let (k, v) = value_str
                .split_once(self.separator as char)
                .unwrap_or((key_str, value_str));
            Some(Box::new(ExampleView {
                key: k.to_string(),
                value: v.to_string(),
            }))
        }
    }

    #[test]
    fn test_decode_produces_a_view() {
        let d = ExampleDecoder::new(b'=');
        let v = d.decode(b"v/k1", b"k1=payload").expect("must decode");
        assert_eq!(v.kind(), "example");
    }

    #[test]
    fn test_decode_malformed_value_returns_none() {
        // Invalid UTF-8 in the value: the decoder must skip (None), not panic.
        let d = ExampleDecoder::new(b'=');
        assert!(d.decode(b"v/bad", &[0xFF, 0xFE, 0xFD]).is_none());
    }

    #[test]
    fn test_decoder_is_object_safe_for_registry_routes() {
        // ViewRegistry routes hold &'static dyn Decoder — object safety is
        // part of the contract.
        let d: &dyn Decoder = &ExampleDecoder::new(b'=');
        assert!(d.decode(b"k", b"k=v").is_some());
    }
}
