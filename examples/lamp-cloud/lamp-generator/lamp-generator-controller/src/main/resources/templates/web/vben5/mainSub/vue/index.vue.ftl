<script lang="ts" setup>
import { onMounted, ref } from 'vue';

import { useFs } from '@fast-crud/fast-crud';

import { getValidateRulesByFs } from '#/api/common/validateByFs';
import { ${table.entityName}Config } from '#/api/${table.plusApplicationName}/${table.plusModuleName}/${table.entityName?uncap_first}';
import { BasicTitle } from '#/components/basic';
import { ActionEnum } from '#/enums/commonEnum';

import { createCrudOptions, frontRules } from './data/crud';
import SubTable from './modules/index.vue';

defineOptions({
  name: '${table.menuName}',
  inheritAttrs: false,
});
const subTableRef = ref();
const { crudRef, crudBinding, crudExpose, appendCrudOptions } = useFs({
  createCrudOptions,
  context: { subTableRef },
});

// 页面打开后获取列表数据
onMounted(async () => {
  const addFormOptions = await getValidateRulesByFs({
    Api:  ${table.entityName}Config.Save,
    mode: ActionEnum.ADD,
    customRules: frontRules(crudExpose, ActionEnum.ADD),
  });
  const editFormOptions = await getValidateRulesByFs({
    Api:  ${table.entityName}Config.Update,
    mode: ActionEnum.EDIT,
    customRules: frontRules(crudExpose, ActionEnum.EDIT),
  });
  appendCrudOptions({ ...addFormOptions, ...editFormOptions });
  crudExpose.doRefresh();
});
</script>

<template>
  <FsPage>
    <FsCrud ref="crudRef" v-bind="crudBinding">
      <template #form-body-top>
        <BasicTitle span line style="margin-bottom: 1rem">${table.swaggerComment}</BasicTitle>
      </template>
      <template #form-body-bottom>
        <BasicTitle span line style="margin-bottom: 1rem">${sub.table.swaggerComment}</BasicTitle>
        <SubTable ref="subTableRef" />
      </template>
    </FsCrud>
  </FsPage>
</template>
