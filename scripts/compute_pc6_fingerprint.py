#!/usr/bin/env python3
"""Compute the PC6 Track 2 contract fingerprint (PC6 §5, re-ratification 9b2ffd4b item 2)."""
import hashlib

VOCAB_MAP_SHA = "0a87a32e8e8688fbd542a1023058cc79e67e4262fc286c84754fcb85a56268b7"
SECTIONS = "facts,entities,concepts,propositions,authority_bindings,state_vectors"
FROZEN = "describes,evaluated,instantiates,identified_by,governed_by,evidences"
DDL = open("sql/V158__pc6_track2_metadata_ddl.sql", "rb").read()

payload = f"PC6-TRACK2|{VOCAB_MAP_SHA}|sections={SECTIONS}|frozen={FROZEN}|ddl=".encode() + hashlib.sha256(DDL).hexdigest().encode()
print("composite contract fingerprint:")
print(hashlib.sha256(payload).hexdigest())
print("ddl sha256:", hashlib.sha256(DDL).hexdigest())
