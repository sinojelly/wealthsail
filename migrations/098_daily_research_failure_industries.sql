-- Only server taxonomy codes may accompany a terminal failure diagnosis.
-- Historical records remain NULL; runtime grants and immutable results stay unchanged.
ALTER TABLE research.daily_research_results
 ADD COLUMN failure_industry_codes TEXT[],
 ADD CONSTRAINT daily_research_failure_industry_codes_check CHECK (
   failure_industry_codes IS NULL OR (
     array_ndims(failure_industry_codes)=1
     AND cardinality(failure_industry_codes) BETWEEN 1 AND 10
     AND array_position(failure_industry_codes,NULL) IS NULL
     AND failure_industry_codes <@ ARRAY[
       'ACTIVE_GROWTH','AEROSPACE','AI','BANK','BROAD','BROKER','CHEMICAL',
       'COAL','COMMUNICATION','CONSUMPTION','DEFENCE','DIVIDEND','DRUG',
       'FINANCE_POLICY','FINANCIAL','FINTECH','GAMING','GLOBAL_ENERGY',
       'GOLD','GOLD_ENERGY','HEALTHCARE_CLINICAL','HK_TECH','INNOVATIVE_DRUG',
       'LOW_ALTITUDE','MEMORY','NONFERROUS','PCB','POWER','RARE_EARTH',
       'RARE_EARTH_METALS','ROBOT','ROBOTICS_MOBILITY','SATELLITE',
       'SEMICONDUCTOR_AI','SOLID_BATTERY','TRADITIONAL_MEDICINE','US_TECH'
     ]::TEXT[])),
 ADD CONSTRAINT daily_research_ready_industry_codes_check CHECK (
   status<>'READY' OR failure_industry_codes IS NULL);

COMMENT ON COLUMN research.daily_research_results.failure_industry_codes IS
 'Bounded server-standard industry codes only; never model labels, raw messages or private text. Administrator API projection only.';
