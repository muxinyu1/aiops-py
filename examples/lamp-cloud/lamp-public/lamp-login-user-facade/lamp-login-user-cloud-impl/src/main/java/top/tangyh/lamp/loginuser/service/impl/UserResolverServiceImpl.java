package top.tangyh.lamp.loginuser.service.impl;

import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Component;
import top.tangyh.basic.base.R;
import top.tangyh.lamp.loginuser.api.BaseApi;
import top.tangyh.lamp.loginuser.api.OauthApi;
import top.tangyh.lamp.loginuser.api.SystemApi;
import top.tangyh.lamp.model.entity.base.SysEmployee;
import top.tangyh.lamp.model.entity.base.SysOrg;
import top.tangyh.lamp.model.entity.base.SysPosition;
import top.tangyh.lamp.model.entity.system.SysUser;
import top.tangyh.lamp.model.vo.result.UserQuery;
import top.tangyh.lamp.userinfo.service.UserResolverService;

import java.util.List;

/**
 * 用户注入 cloud版本实现
 *
 * @author zuihou
 * @since 2024年09月20日14:15:47
 *
 */
@Component
@RequiredArgsConstructor
public class UserResolverServiceImpl implements UserResolverService {
    private final BaseApi baseApi;
    private final OauthApi oauthApi;
    private final SystemApi systemApi;

    @Override
    public R<SysUser> getById(UserQuery query) {
        Long userId = query.getUserId();
        R<SysUser> userResult = systemApi.getUserById(userId);
        if (!userResult.getIsSuccess() || userResult.getData() == null) {
            return R.success(new SysUser());
        }

        SysUser sysUser = userResult.getData();
        boolean notEmptyEmployee = notEmpty(query.getEmployeeId());
        boolean queryEmployee = query.getFull() || query.getEmployee();
        boolean queryOrg = query.getFull() || query.getOrg();
        boolean queryCurrentOrg = query.getFull() || query.getCurrentOrg();
        boolean queryPosition = query.getFull() || query.getPosition();
        boolean queryResource = query.getFull() || query.getResource();
        boolean queryRoles = query.getFull() || query.getRoles();
        boolean anyQuery = queryEmployee || queryOrg || queryCurrentOrg || queryPosition || queryResource || queryRoles;
        if (notEmptyEmployee && anyQuery) {
            R<SysEmployee> employeeResult = baseApi.getEmployeeById(query.getEmployeeId());
            if (!employeeResult.getIsSuccess() || employeeResult.getData() == null) {
                return R.success(sysUser);
            }
            SysEmployee baseEmployee = employeeResult.getData();
            sysUser.setEmployee(baseEmployee);
            // 当前单位
            if (queryCurrentOrg && notEmpty(baseEmployee.getLastCompanyId())) {
                R<SysOrg> baseOrgResult = baseApi.getOrgById(baseEmployee.getLastCompanyId());
                sysUser.setCompany(baseOrgResult.getData());
            }
            // 当前部门
            if (queryCurrentOrg && notEmpty(baseEmployee.getLastDeptId())) {
                R<SysOrg> baseOrgResult = baseApi.getOrgById(baseEmployee.getLastDeptId());
                sysUser.setDept(baseOrgResult.getData());
            }
            // 他所在的 单位和部门
            if (queryOrg) {
                R<List<SysOrg>> companyList = baseApi.findCompanyByEmployeeId(baseEmployee.getId());
                sysUser.setCompanyList(companyList.getData());

                R<List<SysOrg>> deptList = baseApi.findDeptByEmployeeId(baseEmployee.getId(), baseEmployee.getLastCompanyId());
                sysUser.setDeptList(deptList.getData());
            }
            // 岗位
            if (queryPosition && notEmpty(baseEmployee.getPositionId())) {
                R<SysPosition> positionResult = baseApi.getPositionById(baseEmployee.getPositionId());
                sysUser.setPosition(positionResult.getData());
            }
            // 资源
            if (queryResource) {
                R<List<String>> resources = oauthApi.findVisibleResource(baseEmployee.getId(), null);
                sysUser.setResourceCodeList(resources.getData());
            }
            // 角色
            if (queryRoles) {
                R<List<String>> codes = baseApi.findRoleCodeByEmployeeId(baseEmployee.getId());
                sysUser.setRoleCodeList(codes.getData());
            }
        }
        return R.success(sysUser);
    }

    private boolean notEmpty(Long val) {
        return val != null && !Long.valueOf(0).equals(val);
    }
}
