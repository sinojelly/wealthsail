-- =============================================================================
-- WealthSail Migration 026: 企业微信回调持久防重放
--
-- 回调在验签、解密后先 claim 一个不含明文 CorpID/MsgId 的 SHA-256 指纹。
-- 表是系统级安全状态，不属于任何租户，也不向 API/浏览器/Worker 暴露；API
-- 只能调用固定 search_path 的 SECURITY DEFINER 函数完成原子 claim。
-- =============================================================================

CREATE TABLE workflow.wecom_callback_claims (
    event_fingerprint TEXT PRIMARY KEY,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    expires_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT wecom_callback_fingerprint_format
        CHECK (event_fingerprint ~ '^sha256:[0-9a-f]{64}$'),
    CONSTRAINT wecom_callback_claim_expiry
        CHECK (expires_at > first_seen_at)
);

CREATE INDEX idx_wecom_callback_claims_expiry
    ON workflow.wecom_callback_claims (expires_at);

REVOKE ALL ON workflow.wecom_callback_claims
    FROM PUBLIC, authenticated, wealthsail_api, wealthsail_worker;

CREATE FUNCTION workflow.claim_wecom_callback(p_event_fingerprint TEXT)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = workflow, pg_catalog
AS $$
DECLARE
    v_claimed BOOLEAN := FALSE;
BEGIN
    IF p_event_fingerprint IS NULL
       OR p_event_fingerprint !~ '^sha256:[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'WECOM_CALLBACK_FINGERPRINT_INVALID'
            USING ERRCODE = '22023';
    END IF;

    -- The signed callback itself is accepted only inside a 300-second window.
    -- Retaining claims for the same interval prevents a second process from
    -- executing the authenticated message while bounding table growth.
    DELETE FROM workflow.wecom_callback_claims
     WHERE expires_at <= clock_timestamp();

    INSERT INTO workflow.wecom_callback_claims
        (event_fingerprint, first_seen_at, expires_at)
    VALUES
        (p_event_fingerprint, clock_timestamp(),
         clock_timestamp() + INTERVAL '300 seconds')
    ON CONFLICT (event_fingerprint) DO NOTHING
    RETURNING TRUE INTO v_claimed;

    RETURN COALESCE(v_claimed, FALSE);
END;
$$;

REVOKE ALL ON FUNCTION workflow.claim_wecom_callback(TEXT)
    FROM PUBLIC, authenticated;
GRANT EXECUTE ON FUNCTION workflow.claim_wecom_callback(TEXT)
    TO wealthsail_api;

COMMENT ON TABLE workflow.wecom_callback_claims IS
    'Opaque, short-lived fingerprints of authenticated WeCom callbacks; no direct application-role access.';
COMMENT ON FUNCTION workflow.claim_wecom_callback(TEXT) IS
    'Atomically returns true only for the first authenticated callback fingerprint inside the replay window.';
