/**
 * VIOLATION DEMO — C1 shallow hierarchy (depth <= 3).
 *
 * TypeSpec permits unlimited extends depth; the shrapnel catalog does not.
 * Expected outcome: the emitted migration FAILS at apply time when the
 * database computes depth 4 for Echo (ck_sterev_depth_range) and the whole
 * transaction aborts. This is the oracle property: the DB, not the emitter,
 * is the invariant's enforcement point.
 */
namespace Violation;

model Alpha {
  a: string;
}

@doc("Extends Alpha: tier 2 of the depth demo.")
model Beta extends Alpha {
  b: string;
}

@doc("Extends Beta: tier 3 of the depth demo.")
model Gamma extends Beta {
  c: string;
}

@doc("Extends Gamma: tier 4 of the depth demo (depth 3, still legal).")
model Delta extends Gamma {
  d: string;
}

@doc("Extends Delta: tier 5 of the depth demo — depth 4, C1 violation.")
model Echo extends Delta {
  e: string;
}
