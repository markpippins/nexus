import "@typespec/http";

using Http;

/**
 * Nexus User API — shared user models + the live user-access surface.
 *
 * DTOs are framework-neutral and match the JVM shared module
 * `jvm/shared/user-api` (com.aibizarchitect.nexus.v1.user). Field names
 * follow the JVM source of truth: Jackson serializes the DTO fields as
 * declared (`alias`, not `userAlias` — UserDTO.java / UserRegistrationDTO.java).
 *
 * Operations are reverse-engineered from the helidon user-access-service
 * (jvm/helidon/user-access-service, UserResource.java): two routes total,
 * both credential validation. @Consumes form-urlencoded on POST, JSON on
 * success; 401 (empty body) when validation fails. AS SHIPPED per the
 * audit-8bfe6519 convention.
 *
 * NOUN SPLIT (contract content, feeds the sweep's normalization list N12):
 * the credential noun is `alias` on the wire for the DTOs and the helidon
 * query/form params, but the spring side spells it `userAlias` (JPA entity
 * UserRegistration.userAlias, service-broker user-access contract, RestFsService).
 * The two spellings refer to the same semantic; a normalization ruling is
 * pending — do NOT add a third spelling.
 */
@service(#{ title: "Nexus User API" })
namespace com.aibizarchitect.nexus.v1.user;

// ── DTOs (JVM-serialization fidelity) ───────────────────────────────────

/** Core user data transfer object (com.aibizarchitect.nexus.v1.user.UserDTO). */
model UserDTO {
  id?: string;
  /** User login alias — serialized as `alias` (JVM field name). Backtick-escaped: alias is a TypeSpec keyword. */
  `alias`: string;
  email?: string;
  admin?: boolean = false;
  avatarUrl?: string;
}

/** User registration data transfer object (UserRegistrationDTO). */
model UserRegistrationDTO {
  id?: string;
  /** User login alias — serialized as `alias` (JVM field name). Backtick-escaped: alias is a TypeSpec keyword. */
  `alias`: string;
  email?: string;
  admin?: boolean = false;
}

/**
 * 401 body is EMPTY in the helidon implementation (Response.status(UNAUTHORIZED)
 * with no entity) — an @error model with pinned status so the union splits
 * into a real 401 response in emitted specs instead of folding into 200.
 */
@error
model Unauthorized {
  @statusCode statusCode: 401;
}

// ── Operations (helidon user-access-service, UserResource.java) ─────────

@route("/user")
@tag("User Access")
interface UserAccess {
  /**
   * Validate credentials by alias + identifier (password). Form-encoded so
   * the secret never lands in access logs or URLs.
   * Returns the UserRegistrationDTO on success; 401 empty on failure.
   */
  @post
  @route("/validate")
  validate(
    @header contentType: "application/x-www-form-urlencoded",
    @body body: ValidateForm,
  ): UserRegistrationDTO | Unauthorized;
}

/**
 * The helidon resource also exposes GET /user/validate with the same
 * semantics via query params. It is a live route but is deliberately NOT
 * declared as a first-class operation here: passing credentials in a URL
 * is the leak-prone variant, and the contract should not advertise it as
 * the idiomatic call. Clients wanting it can use the documented
 * `?alias=&identifier=` shape; the POST form is the contract surface.
 */

/** Form body for POST /user/validate (FormParam names are wire-verbatim). */
model ValidateForm {
  /** Credential alias — helidon FormParam `alias` (spring spells it `userAlias`; see NOUN SPLIT). */
  `alias`: string;
  /** Secret — helidon FormParam name is `identifier`. */
  identifier: string;
}
