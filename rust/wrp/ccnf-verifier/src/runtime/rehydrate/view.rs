pub trait View {
    fn kind(&self) -> &'static str;
}

pub struct ExampleView {
    pub key: String,
    pub value: String,
}

impl View for ExampleView {
    fn kind(&self) -> &'static str {
        "example"
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // Rehydration views are read-only: the rehydrate layer reconstructs
    // state FOR reading, never mutates the snapshot it came from.
    #[test]
    fn test_example_view_reports_its_kind() {
        let v = ExampleView {
            key: "v/k1".to_string(),
            value: "payload".to_string(),
        };
        assert_eq!(v.kind(), "example");
        assert_eq!(v.key, "v/k1");
        assert_eq!(v.value, "payload");
    }

    #[test]
    fn test_view_is_object_safe_for_registry_boxes() {
        // ViewRegistry hands back Box<dyn View>; this compiles only if the
        // trait remains object-safe. Guard the contract with a real box.
        let v: Box<dyn View> = Box::new(ExampleView {
            key: "k".to_string(),
            value: "v".to_string(),
        });
        assert_eq!(v.kind(), "example");
    }
}
