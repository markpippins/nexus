package com.aibizarchitect.nexus.shrapnel.model.value;

import lombok.Getter;
import lombok.Setter;
import lombok.extern.slf4j.Slf4j;

import jakarta.persistence.*;
import java.util.Objects;

import jakarta.persistence.*;

@Slf4j
@Getter
@Setter
@Entity
@Table(name = "value", schema = "shrapnel")
public class Value {

	@ManyToOne
	@JoinColumn(name = "value_type_code")
	private ValueTypeEnum valueType;

	@Id
	@GeneratedValue(strategy = GenerationType.AUTO)
	@Column(name = "id", nullable = false)
	private Long id;

	public ValueTypeEnum getType() {
		return valueType;
	}
}