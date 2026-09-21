package com.aibizarchitect.nexus.shrapnel.model.db;

import java.util.Objects;

import com.aibizarchitect.nexus.shrapnel.field.FieldTypeEnum;
import com.aibizarchitect.nexus.shrapnel.field.IField;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.JoinColumn;
import jakarta.persistence.ManyToOne;
import jakarta.persistence.Table;
import lombok.Getter;
import lombok.Setter;

@Getter
@Setter
@Entity
@Table(name = "field", schema = "shrapnel")
public class DBField implements IField {

	@ManyToOne
	@JoinColumn(name = "field_type_code")
	private FieldTypeEnum fieldType;

	@Id
	@GeneratedValue(strategy = GenerationType.AUTO)
	@Column(name = "id", nullable = false)
	private Long id;

	@Column(name = "name", nullable = false)
	private String name;

	@Column(name = "property_name", nullable = false)
	private String propertyName;

	@Column(name = "label", nullable = false)
	private String label;

	@Column(name = "field_index", nullable = false)
	private Integer index;

	@Column(name = "is_calculated", nullable = false)
	private Boolean calculated = false;

	@Override
	public FieldTypeEnum getType() {
		return fieldType;
	}
}