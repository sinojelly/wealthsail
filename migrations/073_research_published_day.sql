BEGIN;
CREATE FUNCTION reference.research_available_day(p_hour INTEGER, p_as_of DATE, p_now TIMESTAMPTZ)
RETURNS DATE LANGUAGE sql STABLE STRICT SET search_path=pg_catalog AS $$
 SELECT CASE
   WHEN p_hour NOT BETWEEN 0 AND 23 OR p_as_of>(p_now AT TIME ZONE 'Asia/Shanghai')::date THEN NULL
   WHEN p_as_of<(p_now AT TIME ZONE 'Asia/Shanghai')::date THEN p_as_of
   WHEN EXTRACT(HOUR FROM p_now AT TIME ZONE 'Asia/Shanghai')>=p_hour THEN p_as_of
   ELSE p_as_of-1 END;
$$;
REVOKE ALL ON FUNCTION reference.research_available_day(INTEGER,DATE,TIMESTAMPTZ) FROM PUBLIC,authenticated;
GRANT EXECUTE ON FUNCTION reference.research_available_day(INTEGER,DATE,TIMESTAMPTZ) TO wealthsail_api,wealthsail_worker;
COMMIT;
