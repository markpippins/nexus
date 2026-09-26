/**
 * Sample catalog authoring surface for the tsp-eav-emitter prototype.
 * No protocol libraries needed — the emitter walks models only.
 */
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

@doc(
  "Extends Person because verification is a distinct lifecycle stage with its own evidence, not a flag on the base record."
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

  @doc("Credential score, if scored.")
  score?: float64;

  @doc("Raw credential payload.")
  payload?: Record<unknown>;
}
