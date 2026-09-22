package com.aibizarchitect.nexus.shrapnel.repository.value;

import org.springframework.data.jpa.repository.JpaRepository;

import com.aibizarchitect.nexus.shrapnel.model.value.Value;

public interface ValueRepository extends JpaRepository< Value, Long > {
}