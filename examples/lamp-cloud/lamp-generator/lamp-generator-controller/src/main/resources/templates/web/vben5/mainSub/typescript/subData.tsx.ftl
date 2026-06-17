import type { VxeGridProps } from '@vben/plugins/vxe-table';

import type { VbenFormProps } from '#/adapter/form';
import type { ${sub.table.entityName}Model } from '#/api/${sub.table.plusApplicationName}/${sub.table.plusModuleName}/model/${sub.table.entityName?uncap_first}Model';

import { $t } from '@vben/locales';

// 表格参数定义
export const gridSchemas: VxeGridProps<${sub.table.entityName}Model.${sub.table.entityName}ResultVO> = {
  toolbarConfig: {
    perfect: true,
    buttons: [
      { code: 'myInsert', name: $t('common.title.add') },
      {
        code: 'batchDelete',
        name: $t('common.title.delete'),
        status: 'danger',
      },
    ],
  },
  editConfig: {
    trigger: 'click',
    mode: 'row',
    showStatus: true,
    autoClear: true,
  },
  sortConfig: { defaultSort: { field: 'sortValue', order: 'desc' } },
  columns: [
    { type: 'checkbox', width: 50 },
    { title: $t('common.seq'), type: 'seq', width: 50 },
  <#list sub.fields as field>
    <#if field.isList && !field.isLogicDeleteField>
    {
      field: '${field.javaField}',
      title: $t('${sub.table.plusApplicationName}.${sub.table.plusModuleName}.${sub.table.entityName?uncap_first}.${field.javaField}'),
      editRender: { name: 'Input', attrs: { placeholder: $t('common.inputText') } },
    },
    </#if>
  </#list>
    {
      field: 'createdTime',
      formatter: 'formatDateTime',
      sortable: true,
      width: 180,
      title: $t('common.createdTime'),
    },
    {
      field: 'action',
      fixed: 'right',
      title: '操作',
      slots: { default: 'ACTION' },
      width: 150,
    },
  ],
};

// 搜索表单参数定义
export const searchFormSchemas: VbenFormProps = {
  schema: [
  <#list sub.fields as field>
    <#if field.isQuery && !field.isLogicDeleteField>
    {
      component: '${field.component!'Input'}',
      fieldName: '${field.javaField}',
      label: $t('${sub.table.plusApplicationName}.${sub.table.plusModuleName}.${sub.table.entityName?uncap_first}.${field.javaField}'),
      componentProps: {
        placeholder: $t('common.inputText'),
        allowClear: true,
      },
    },
    </#if>
  </#list>
  ],
};
