import "@typespec/http";
import "./models.tsp";
import "./operations.tsp";
using Http;
@service(#{ title: "Rest FS Service" }) 
namespace restFsService;
