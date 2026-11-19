# TypeSpec Modular Structure

## Folder Structure

```
typespec/
├── main.tsp                  # Entry point - imports and service decorator
├── fs/
│   ├── models.tsp            # All model definitions (FsItem, FsRequest, etc.)
│   └── operations.tsp        # All operation definitions (listFiles, move, etc.)
├── tsp-output/               # Generated code
│   ├── schema/               # OpenAPI 3.1.0 schema
│   ├── clients/
│   │   ├── java/             # Java client (see limitations below)
│   │   └── js/               # JavaScript/TypeScript client
│   └── server/
│       └── js/               # JavaScript server stubs
└── tspconfig.yaml            # Emitter configuration
```

## File Contents

### `main.tsp`

```typespec
import "@typespec/http";
import "./fs/models.tsp";
import "./fs/operations.tsp";
using Http;
@service(#{ title: "Rest FS Service" }) namespace restFsService;
```

**Note**: Imports MUST come before `using` statements and the `@service` decorator due to TypeSpec compiler requirements.

### `fs/models.tsp`
- `FsItem` - File/directory metadata
- `FsListResponse` - List operation response
- `FsItemReference` - Batch operation item reference
- `FsOperationResponse` - Generic operation response
- `FsRequest` - Request model with token/alias support

### `fs/operations.tsp`
All 13 REST endpoints:
- `/list`, `/cd`, `/mkdir`, `/rmdir`
- `/touch`, `/rm`, `/rename`, `/rename-item`
- `/copy`, `/move`, `/move-items`
- `/has-file`, `/has-folder`

## Emitter Status

| Emitter | Status | Notes |
|---------|--------|-------|
| `@typespec/openapi3` | ✅ Works | Generates OpenAPI 3.1.0 schema |
| `@typespec/http-client-js` | ✅ Works | Generates TypeScript client |
| `@typespec/http-server-js` | ✅ Works | Generates Node.js server stubs |
| `@typespec/http-client-java` | ⚠️ Limited | Works with monolithic spec only |
| `@typespec/http-client-python` | ❌ Disabled | Has issues with empty namespaces |

## Java Emitter Limitation

The Java emitter has a known issue with modular TypeSpec structures - it generates empty package-info.java files instead of the full client code.

### Workaround

The Java client files were generated **before** modularization and copied to:
- `spring/service-broker/file-service/src/main/java/restfsservice/`

These files remain valid because:
1. The API contract (models + operations) hasn't changed
2. Only the file organization changed, not the spec content

### To Regenerate Java Client

**Option 1**: Temporarily consolidate into monolithic spec
```bash
# 1. Copy fs/models.tsp and fs/operations.tsp content into main.tsp
# 2. Run: npx tsp compile .
# 3. Copy generated Java files to Spring project
# 4. Restore modular main.tsp
```

**Option 2**: Use separate spec for Java
```bash
# Create typespec/main-java.tsp with monolithic structure
# Run: npx tsp compile main-java.tsp --emit "@typespec/http-client-java"
```

## Benefits of Modular Structure

1. **Maintainability**: Easier to navigate and update
2. **Separation of Concerns**: Models and operations in separate files
3. **Scalability**: Can add more feature folders (e.g., `fs/auth.tsp`, `fs/batch.tsp`)
4. **Team Collaboration**: Multiple developers can work on different files

## Migration Notes

### What Changed
- File organization only (content unchanged)
- Import structure in `main.tsp`

### What Didn't Change
- API contract (all 13 operations identical)
- Model definitions (same fields and types)
- Generated client APIs (same methods and signatures)
- Spring/Node implementations (no code changes required)

### Testing
```bash
# Verify compilation
cd typespec
npx tsp compile .

# Check generated JS client
cat tsp-output/clients/js/src/restFsServiceClient.ts

# Check generated OpenAPI
cat tsp-output/schema/openapi.yaml
```

## Future Enhancements

1. **Add authentication models** in `fs/auth.tsp`
2. **Add batch operation models** in `fs/batch.tsp`
3. **Add pagination support** in `fs/pagination.tsp`
4. **Enable Java emitter** when microsoft/typespec issue is resolved
