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
package org.springblade.system.feign;

import org.springblade.core.launch.constant.AppConstant;
import org.springframework.cloud.openfeign.FeignClient;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestParam;

import java.util.List;

/**
 * 接口权限Feign接口类
 *
 * @author Chill
 */
@FeignClient(value = AppConstant.APPLICATION_SYSTEM_NAME, fallback = IApiScopeClientFallback.class)
public interface IApiScopeClient {

	String API_PREFIX = "/feign/client/api-scope";
	String PERMISSION_PATH = API_PREFIX + "/permission-path";
	String PERMISSION_CODE = API_PREFIX + "/permission-code";

	/**
	 * 获取接口权限地址
	 *
	 * @param roleId 角色id
	 * @return permissions
	 */
	@GetMapping(PERMISSION_PATH)
	List<String> permissionPath(@RequestParam("roleId") String roleId);

	/**
	 * 获取接口权限信息
	 *
	 * @param permission 权限编号
	 * @param roleId     角色id
	 * @return permissions
	 */
	@GetMapping(PERMISSION_CODE)
	List<String> permissionCode(@RequestParam("permission") String permission, @RequestParam("roleId") String roleId);

}
