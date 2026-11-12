-- Recorded for STU-022 (tests/security/test_definitions.py): pg_dump 16.15 --schema-only --no-owner
-- --no-privileges -n studio_ddl_measurements, PostgreSQL 16.15; the \restrict lines (a random key) removed and the
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
-- Name: studio_ddl_measurements; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA studio_ddl_measurements;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: measurement; Type: TABLE; Schema: studio_ddl_measurements; Owner: -
--

CREATE TABLE studio_ddl_measurements.measurement (
    device_id character varying(64) NOT NULL,
    channel character varying(64) NOT NULL,
    ts_utc timestamp with time zone NOT NULL,
    value double precision NOT NULL,
    unit character varying(16) NOT NULL,
    quality smallint NOT NULL,
    source_seq bigint NOT NULL
);


--
-- Name: measurement measurement_pkey; Type: CONSTRAINT; Schema: studio_ddl_measurements; Owner: -
--

ALTER TABLE ONLY studio_ddl_measurements.measurement
    ADD CONSTRAINT measurement_pkey PRIMARY KEY (device_id, channel, ts_utc);


--
-- PostgreSQL database dump complete
--
