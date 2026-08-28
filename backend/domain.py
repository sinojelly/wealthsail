"""HTTP-layer adapters for canonical backend domain services.

Service construction stays explicit so routes, integrations and tests use the
same implementation and security settings.
"""

from backend.common.tenancy import TenantContext as HttpTenantContext
from backend.config import Settings
from backend.identity.domain_service import (
    IdentityService,
    IdentitySettings,
    TenantContext as DomainTenantContext,
)


def identity_service(pool, settings: Settings) -> IdentityService:
    return IdentityService(
        pool,
        IdentitySettings(
            jwt_secret=settings.jwt_secret,
            refresh_token_hmac_key=settings.refresh_token_hmac_key,
            binding_hmac_key=settings.binding_hmac_key,
            password_reset_hmac_key=settings.password_reset_hmac_key,
            routing_encryption_key=settings.routing_encryption_key,
            access_token_ttl_seconds=settings.access_token_ttl_seconds,
            refresh_token_ttl_days=settings.refresh_token_ttl_days,
            binding_token_ttl_seconds=settings.binding_token_ttl_seconds,
            binding_token_max_attempts=settings.binding_token_max_attempts,
            password_reset_token_ttl_minutes=settings.password_reset_token_ttl_minutes,
            jwt_algorithm=settings.jwt_algorithm,
            auth_dev_mode=False,
        ),
    )


def domain_tenant(ctx: HttpTenantContext) -> DomainTenantContext:
    return DomainTenantContext(
        tenant_id=ctx.tenant_id,
        user_id=ctx.user_id,
        tenant_role=ctx.tenant_role,
    )
