-- Recorded for STU-022 (tests/security/test_definitions.py): pg_dump 16.15 --schema-only --no-owner
-- --no-privileges -n studio_ddl_parts, PostgreSQL 16.15; the \restrict lines (a random key) removed and the
-- test role's name replaced with studio_ddl_role. Regenerate from create_schemas() of that test.
--
-- PostgreSQL database dump
--


-- Dumped from database version 16.15 (Ubuntu 16.15-0ubuntu0.24.04.1)
-- Dumped by pg_dump version 16.15 (Ubuntu 16.15-0ubuntu0.24.04.1)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: studio_ddl_parts; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA studio_ddl_parts;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: material; Type: TABLE; Schema: studio_ddl_parts; Owner: -
--

CREATE TABLE studio_ddl_parts.material (
    id bigint NOT NULL,
    code character varying(32) NOT NULL,
    name character varying(200) NOT NULL,
    created_at timestamp with time zone NOT NULL,
    CONSTRAINT ck_material_code CHECK (((length((code)::text) >= 1) AND (length((code)::text) <= 32))),
    CONSTRAINT ck_material_name CHECK (((length((name)::text) >= 1) AND (length((name)::text) <= 200)))
);


--
-- Name: material_id_seq; Type: SEQUENCE; Schema: studio_ddl_parts; Owner: -
--

ALTER TABLE studio_ddl_parts.material ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME studio_ddl_parts.material_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: part; Type: TABLE; Schema: studio_ddl_parts; Owner: -
--

CREATE TABLE studio_ddl_parts.part (
    id bigint NOT NULL,
    part_number character varying(40) NOT NULL,
    name character varying(200) NOT NULL,
    material_id bigint NOT NULL,
    status character varying(16) DEFAULT 'draft'::character varying NOT NULL,
    version integer DEFAULT 1 NOT NULL,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    CONSTRAINT ck_part_name CHECK (((length((name)::text) >= 1) AND (length((name)::text) <= 200))),
    CONSTRAINT ck_part_part_number CHECK (((length((part_number)::text) >= 1) AND (length((part_number)::text) <= 40))),
    CONSTRAINT ck_part_status CHECK (((status)::text = ANY ((ARRAY['draft'::character varying, 'released'::character varying, 'blocked'::character varying, 'obsolete'::character varying])::text[]))),
    CONSTRAINT ck_part_version CHECK ((version >= 1))
);


--
-- Name: part_id_seq; Type: SEQUENCE; Schema: studio_ddl_parts; Owner: -
--

ALTER TABLE studio_ddl_parts.part ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME studio_ddl_parts.part_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: part_relation; Type: TABLE; Schema: studio_ddl_parts; Owner: -
--

CREATE TABLE studio_ddl_parts.part_relation (
    assembly_id bigint NOT NULL,
    component_id bigint NOT NULL,
    quantity numeric(12,3) NOT NULL,
    CONSTRAINT ck_part_relation_not_self CHECK ((assembly_id <> component_id)),
    CONSTRAINT ck_part_relation_quantity CHECK ((quantity > (0)::numeric))
);


--
-- Name: property; Type: TABLE; Schema: studio_ddl_parts; Owner: -
--

CREATE TABLE studio_ddl_parts.property (
    id bigint NOT NULL,
    part_id bigint NOT NULL,
    key character varying(64) NOT NULL,
    value numeric(18,6) NOT NULL,
    unit character varying(16) NOT NULL,
    CONSTRAINT ck_property_key CHECK (((length((key)::text) >= 1) AND (length((key)::text) <= 64))),
    CONSTRAINT ck_property_unit CHECK (((length((unit)::text) >= 1) AND (length((unit)::text) <= 16)))
);


--
-- Name: property_id_seq; Type: SEQUENCE; Schema: studio_ddl_parts; Owner: -
--

ALTER TABLE studio_ddl_parts.property ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME studio_ddl_parts.property_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: material pk_material; Type: CONSTRAINT; Schema: studio_ddl_parts; Owner: -
--

ALTER TABLE ONLY studio_ddl_parts.material
    ADD CONSTRAINT pk_material PRIMARY KEY (id);


--
-- Name: part pk_part; Type: CONSTRAINT; Schema: studio_ddl_parts; Owner: -
--

ALTER TABLE ONLY studio_ddl_parts.part
    ADD CONSTRAINT pk_part PRIMARY KEY (id);


--
-- Name: part_relation pk_part_relation; Type: CONSTRAINT; Schema: studio_ddl_parts; Owner: -
--

ALTER TABLE ONLY studio_ddl_parts.part_relation
    ADD CONSTRAINT pk_part_relation PRIMARY KEY (assembly_id, component_id);


--
-- Name: property pk_property; Type: CONSTRAINT; Schema: studio_ddl_parts; Owner: -
--

ALTER TABLE ONLY studio_ddl_parts.property
    ADD CONSTRAINT pk_property PRIMARY KEY (id);


--
-- Name: material uq_material_code; Type: CONSTRAINT; Schema: studio_ddl_parts; Owner: -
--

ALTER TABLE ONLY studio_ddl_parts.material
    ADD CONSTRAINT uq_material_code UNIQUE (code);


--
-- Name: part uq_part_part_number; Type: CONSTRAINT; Schema: studio_ddl_parts; Owner: -
--

ALTER TABLE ONLY studio_ddl_parts.part
    ADD CONSTRAINT uq_part_part_number UNIQUE (part_number);


--
-- Name: property uq_property_part_id_key; Type: CONSTRAINT; Schema: studio_ddl_parts; Owner: -
--

ALTER TABLE ONLY studio_ddl_parts.property
    ADD CONSTRAINT uq_property_part_id_key UNIQUE (part_id, key);


--
-- Name: ix_part_material_id; Type: INDEX; Schema: studio_ddl_parts; Owner: -
--

CREATE INDEX ix_part_material_id ON studio_ddl_parts.part USING btree (material_id);


--
-- Name: ix_part_relation_component_id; Type: INDEX; Schema: studio_ddl_parts; Owner: -
--

CREATE INDEX ix_part_relation_component_id ON studio_ddl_parts.part_relation USING btree (component_id);


--
-- Name: part fk_part_material; Type: FK CONSTRAINT; Schema: studio_ddl_parts; Owner: -
--

ALTER TABLE ONLY studio_ddl_parts.part
    ADD CONSTRAINT fk_part_material FOREIGN KEY (material_id) REFERENCES studio_ddl_parts.material(id);


--
-- Name: part_relation fk_part_relation_assembly; Type: FK CONSTRAINT; Schema: studio_ddl_parts; Owner: -
--

ALTER TABLE ONLY studio_ddl_parts.part_relation
    ADD CONSTRAINT fk_part_relation_assembly FOREIGN KEY (assembly_id) REFERENCES studio_ddl_parts.part(id) ON DELETE CASCADE;


--
-- Name: part_relation fk_part_relation_component; Type: FK CONSTRAINT; Schema: studio_ddl_parts; Owner: -
--

ALTER TABLE ONLY studio_ddl_parts.part_relation
    ADD CONSTRAINT fk_part_relation_component FOREIGN KEY (component_id) REFERENCES studio_ddl_parts.part(id);


--
-- Name: property fk_property_part; Type: FK CONSTRAINT; Schema: studio_ddl_parts; Owner: -
--

ALTER TABLE ONLY studio_ddl_parts.property
    ADD CONSTRAINT fk_property_part FOREIGN KEY (part_id) REFERENCES studio_ddl_parts.part(id) ON DELETE CASCADE;


--
-- PostgreSQL database dump complete
--
