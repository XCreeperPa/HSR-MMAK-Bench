# Tabulator 6.6.1（本地 vendor）

管理控制台数据页的表格库。**本地锁定版本，不引 CDN**。

| 项 | 值 |
|---|---|
| 库 | Tabulator（tabulator-tables） |
| 版本 | **6.6.1** |
| 许可 | MIT（商用免费） |
| 来源 | npm 包 `tabulator-tables@6.6.1`（`npm pack` 取包，shasum `190c45d3d5be2d30cd66bd426921f8cbcab0d531`） |
| 引入方式 | `<script src="/static/admin/vendor/tabulator-6.6.1/tabulator.min.js">` + `<link rel="stylesheet" href="/static/admin/vendor/tabulator-6.6.1/tabulator.min.css">` |

## 文件与校验

| 文件 | 字节 | SHA-256 |
|---|---|---|
| `tabulator.min.js` | 454026 | `见 tools/check_vendor.py 的输出，或 git show 本文件首次提交` |
| `tabulator.min.css` | 28383 | 同上 |
| `LICENSE` | 1082 | MIT 原文，随包分发 |

校验命令：

```
python tools/check_vendor.py
```

该脚本比对文件哈希与 `tools/vendor-lock.json`，不一致即报错。升级版本时改
`tools/vendor-lock.json` 与本目录文件，并同步更新本文件。

## 为什么是 Tabulator，而不是别的

- 设计稿 `reference/V2正式测试架构设计.md` 第 13.5 节**原本就点名 Tabulator**，
  并给了一张能力清单（列头筛选、全局搜索、多列排序、分组折叠、列显隐、列宽拖动、
  身份列冻结、横纵滚动、虚拟行渲染、复制、下载）。
- 决策记录 D10 当时选择「自建、不引 CDN」的理由是**避免依赖外部网络与第三方脚本**
  （测评服务器在境内网络，管理端是唯一的数据操作面），并已写明后续路径：
  「若要补齐，优先在本地 vendor 一份锁定版本的 Tabulator（不放 CDN）」。
  本目录就是那一步。
- 选型对比见 `reference/V2管理端数据页需求分析.md` 第 5 节。
  落选主因：AG Grid Community 把「逐列显隐面板 / 高级多列排序 / Set Filter / 透视」
  划在 $999/开发者的 Enterprise 里；Handsontable 是非商用免费许可；
  TanStack Table 只是无头内核，虚拟滚动与渲染要全自写；Grid.js / DataTables 能力不够。

## 使用时必须显式关掉的东西

- **单元格编辑、增删行、粘贴写回、改分菜单**：管理端写操作仅限
  「管理标记」与「作废补码」两类（设计稿 13.x）。见 `service/pages/admin/admin.js`。
  关键写法：`clipboard: 'copy'`——Tabulator 6.6 里 `clipboard` 传 `"copy"` 就**不注册 paste 监听**，
  粘贴写回从根上不可能发生。
- **Tabulator 自带的列聚合计算（`calculations`）**：汇总口径由服务端
  `service/admin.py::_summarize()` 按「按选手等权 / 按测试次」两种规则算好后注入，
  库只负责画。否则会踩设计稿 13.5 节「不能误用对全部试次直接求平均的默认列计算」这条红线。

## 6.6.1 的三处硬限制（写下来免得再踩）

1. **冻结只能落在列组上**。`frozenCheck` 会向上递归到父组；
   给**子列**写 `frozen: true` 只会打一条
   `Frozen Column Error - Parent column group must be frozen` 的警告并**静默忽略**。
   要冻结就把 `frozen: true` 写在 `{title, columns: [...]}` 这一层。
2. **没有右侧冻结列**。6.6.1 只有左冻结（`frozen` 只认左），
   「总分」这类右侧关键列钉不住。替代方案是用「列设置」面板一键只留关键列。
3. **没有内置全局搜索框**。v5 的 `search` 表选项在 6.x 被删掉了；
   `setFilter` 走 Filter 模块且函数型过滤器不好持久化。
   本项目的做法是**前端预筛 + `setData`**：搜索/状态/统计资格在数据进表前过滤，
   再与 Tabulator 自己的表头筛选叠加（AND 语义）。

## 另外两个会让人白查半天的写法陷阱

- `persistence.columns` 是「**要留存的列属性名数组**」（如 `['title','width','visible']`），
  **不是** `{width: true, visible: true}` 这种对象。
  传对象会让持久化模块在初始化时对非数组调 `forEach`，整页白屏。
  同理，表头筛选的持久化开关叫 `headerFilter`，不叫 `filter`。
- 表头筛选框会继承页面的全局 `input` 样式（`padding: 10px 12px`），
  65 列的表头会被撑到 38px 高。必须在自己的 CSS 里用 `!important` 压回去。

## 主题

`service/pages/admin/admin.css` 在 Tabulator 默认皮肤之上覆盖配色，
对齐管理端既有的米白 + 深蓝观感，没有另起一套主题。
