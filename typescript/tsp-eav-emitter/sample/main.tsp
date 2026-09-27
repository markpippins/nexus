/**
 * Sample catalog authoring surface for the tsp-eav-emitter prototype.
 * No protocol libraries needed — the emitter walks models only.
 *
 * Decorator demo (storage-class discussion, Discussions thread e4a8dd7f):
 * decorators bind under the ShrapnelCatalog JS namespace, so they are used
 * fully qualified here.
 *   - @ShrapnelCatalog.instanceStorage on CredentialRecord: instances live
 *     in MongoDB (metadata outside the contract — emitted as a registration
 *     INSERT, not a field).
 *   - @ShrapnelCatalog.extendsRationale on VerifiedPerson: the dedicated C2
 *     rationale home (@doc fallback no longer needed).
 *   - @ShrapnelCatalog.calculated on a property: is_calculated = true.
 */
import "tsp-eav-emitter";

@doc("Sample catalog authoring surface for the tsp-eav-emitter prototype.")
namespace Shrapnel;

@doc("A physical person known to the system.")
model Person {
  @doc("Given name.")
  givenName: string;

  @doc("Family name.")
  familyName: string;

  @doc("Date of birth, if recorded.")
  dateOfBirth?: utcDateTime;
}

@ShrapnelCatalog.extendsRationale(
  "Verification is a distinct lifecycle stage with its own evidence, not a flag on the base record."
)
model VerifiedPerson extends Person {
  @doc("When verification completed.")
  verifiedAt: utcDateTime;

  @doc("External verification reference.")
  verificationId: string;
}

@doc("An assertion attaching a verified credential to a person.")
model Credential {
  @doc("Subject of the credential.")
  subjectId: string;

  @doc("Credential kind.")
  kind: string;

  @doc("Credential score, if scored (derived from payload when present).")
  @ShrapnelCatalog.calculated
  score?: float64;

  @doc("Raw credential payload.")
  payload?: Record<unknown>;
}

@ShrapnelCatalog.instanceStorage("mongodb")
@doc("An assertion attaching a verified credential to a person; instances live in MongoDB.")
model CredentialRecord {
  @doc("Subject of the credential.")
  subjectId: string;

  @doc("Stored credential document.")
  document: Record<unknown>;
}
