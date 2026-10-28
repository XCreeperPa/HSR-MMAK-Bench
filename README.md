# HSR-MMAK-Bench v2

《崩坏：星穹铁道》多模态智能体知识基准测试 v2  
Honkai: Star Rail Multimodal Agent Knowledge Benchmark v2

[![Dataset DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.23749374.svg)](https://doi.org/10.5281/zenodo.23749374)
[![Paper DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.23735304.svg)](https://doi.org/10.5281/zenodo.23735304)

作者：**XCreeperPa**。本仓库发布v2的完整核心数据、素材与测试平台源码。

## 发布内容

| 内容 | 数量及入口 |
|---|---|
| 四个模块 | 图片标注、音乐分类、图片综合、视频综合；21题，100分 |
| 实验对象 | 18个智能体配置、2个传统问答脚本、5名活跃玩家 |
| 题目与参考答案 | [questions.json](core/questions.json)、[reference-answers.json](core/reference-answers.json) |
| 匿名作答 | [525个题目位置](core/answers.jsonl)，含已有理由、标注坐标、状态及原分/分析分 |
| 时间与成绩 | [timing.csv](core/timing.csv)、[scores.csv](core/scores.csv) |
| 素材 | [39项索引](core/assets.json)，实际文件在media/media/ |
| 查询与复算 | [SQLite](core/results.sqlite3)、[verify.py](core/verify.py) |
| 平台 | [platform/](platform/)，FastAPI、智能体API、人类答题页和管理页 |

[论文预印本](https://doi.org/10.5281/zenodo.23735304)与[固定版本数据/代码归档](https://doi.org/10.5281/zenodo.23749374)分别维护。Zenodo的1.0.0为本仓库首发依据，压缩包校验信息见[ZENODO-FILES.json](ZENODO-FILES.json)。

## 快速复算

Python 3.12；这一步只依赖标准库：

```sh
python core/verify.py
```

检查525个题目位置的原始及修订分、25组模块/总分、SQLite完整性与素材哈希。图表重建另见[核心数据说明](core/README.md)。

## 本地启动测试平台

```sh
cd platform
python -m pip install -r requirements.txt
python prepare_runtime.py
python -m service.app
```

默认本地地址为`http://127.0.0.1:8770`，任务文档`/task`，管理页`/admin`。准备脚本生成一个示例智能体名册；运行时创建新的本地数据库、密钥和测试码。管理员密码在首次启动时随机生成并打印在本地终端。详细范围与配置见[平台说明](platform/README.md)。

平台支持分布式远程测试：不同环境中的智能体通过六位验证码接入，按任务文档获取题目、素材并依次提交，平台集中记录作答、时间、状态与分数。公开代码不包含本次实验的运行数据库或真实凭据。

## 评分和数据口径

第一模块保留原始头部评分，并另列测试后修订分：姓名与对应头部命中得1分；姓名与对应身体命中且在头部区域外得0.5分；其余0分。每个目标取最高命中档位。第二至第四模块按原选项评分。论文采用修订后的第一模块分数。

平台代码保留实验时的原始线上头部评分；修订分析分由`core/verify.py`及公开判定区域复算。返回了空回答、未提交和输出失败等分别保留；缺失理由和时间没有补造。18个智能体配置与2个传统脚本分别统计。

更多字段定义、缺失状态和计时范围见[数据字典](core/DATA_DICTIONARY.md)。

## 许可与引用

- 原创软件：**MIT**，见[LICENSE](LICENSE)。
- 原创建题文字、参考文本、数据说明与分析：**CC BY 4.0**，见[核心许可说明](core/LICENSE.md)。
- 第三方游戏图片、音频、视频及其他第三方权利保留；不包含在MIT或CC BY授权范围内，见[第三方说明](core/THIRD_PARTY_NOTICES.md)。Tabulator保留其原许可证。

引用数据与代码请使用[数据集DOI](https://doi.org/10.5281/zenodo.23749374)；引用研究论述请使用[论文DOI](https://doi.org/10.5281/zenodo.23735304)。本项目与游戏官方或模型厂商无隶属关系。
