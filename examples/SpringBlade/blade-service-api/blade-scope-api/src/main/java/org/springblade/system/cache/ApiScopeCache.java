/**
 * Copyright (c) 2018-2099, Chill Zhuang 庄骞 (bladejava@qq.com).
 * <p>
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 * <p>
 * http://www.apache.org/licenses/LICENSE-2.0
 * <p>
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */
package org.springblade.system.cache;

import org.springblade.core.cache.utils.CacheUtil;
import org.springblade.core.tool.utils.SpringUtil;
import org.springblade.core.tool.utils.StringPool;
import org.springblade.system.feign.IApiScopeClient;

import java.util.List;

import static org.springblade.core.cache.constant.CacheConstant.SYS_CACHE;

/**
 * 接口权限缓存
 *
 * @author Chill
 */
public class ApiScopeCache {

	private static final String SCOPE_CACHE_ROLE = "apiScope:role:";

	private static final String SCOPE_CACHE_CODE = "apiScope:code:";

	private static IApiScopeClient apiScopeClient;

	private static IApiScopeClient getApiScopeClient() {
		if (apiScopeClient == null) {
			apiScopeClient = SpringUtil.getBean(IApiScopeClient.class);
		}
		return apiScopeClient;
	}

	/**
	 * 获取接口权限地址
	 *
	 * @param roleId 角色id
	 * @return permissions
	 */
	public static List<String> permissionPath(String roleId) {
		List<String> permissions = CacheUtil.get(SYS_CACHE, SCOPE_CACHE_ROLE, roleId, List.class);
		if (permissions == null) {
			permissions = getApiScopeClient().permissionPath(roleId);
			CacheUtil.put(SYS_CACHE, SCOPE_CACHE_ROLE, roleId, permissions);
		}
		return permissions;
	}

	/**
	 * 获取接口权限信息
	 *
	 * @param permission 权限编号
	 * @param roleId     角色id
	 * @return permissions
	 */
	public static List<String> permissionCode(String permission, String roleId) {
		List<String> permissions = CacheUtil.get(SYS_CACHE, SCOPE_CACHE_CODE, permission + StringPool.COLON + roleId, List.class);
		if (permissions == null) {
			permissions = getApiScopeClient().permissionCode(permission, roleId);
			CacheUtil.put(SYS_CACHE, SCOPE_CACHE_CODE, permission + StringPool.COLON + roleId, permissions);
		}
		return permissions;
	}

}
