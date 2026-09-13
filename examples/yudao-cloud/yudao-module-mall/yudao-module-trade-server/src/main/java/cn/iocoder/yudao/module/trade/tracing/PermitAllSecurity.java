package cn.iocoder.yudao.module.trade.tracing;

import org.springframework.stereotype.Component;

/**
 * Dummy permission bean that always allows access.
 * Replaces the real "ss" bean when security auto-config is excluded.
 */
@Component("ss")
public class PermitAllSecurity {

    public boolean hasPermission(String permission) {
        return true;
    }

    public boolean hasAnyPermissions(String... permissions) {
        return true;
    }

    public boolean hasRole(String role) {
        return true;
    }

    public boolean hasAnyRoles(String... roles) {
        return true;
    }
}
