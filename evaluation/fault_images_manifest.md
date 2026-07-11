# 多模态故障图片测试集清单

本清单用于设备检修系统的图片上传、部件识别、故障描述生成和“以图检文”测试。
图片来自 Wikimedia Commons。下载或用于演示时须遵守各图片页面标注的授权要求。

| 编号 | 建议本地文件名 | 测试角色 | 预期部件/现象 | 来源与授权 |
|---|---|---|---|---|
| IMG001 | `normal_spark_plug.jpg` | 正常对照样本 | NGK CR9EK 火花塞；应识别为火花塞，不应无依据判定损坏 | [Sparkplug3.jpg](https://commons.wikimedia.org/wiki/File:Sparkplug3.jpg)，Ren206，Public Domain |
| IMG002 | `burnt_spark_plug.jpg` | 故障样本 | 烧蚀火花塞；可提取“火花塞、烧蚀/过热、积碳”等候选描述 | [Zündkerze abgebrannt.jpg](https://www.eobdcode.com/wp-content/uploads/2023/01/spark-plug-looks-burnt-678x430_thumbnail.jpg)，Red Rooster，CC BY-SA 3.0 / GFDL |
| IMG003 | `overheated_damaged_piston.jpg` | 故障样本 | 发动机缺冷却液运行后的损坏活塞；可识别活塞、过热和表面损伤 | [Damaged piston from a Subaru Impreza boxer engine-2011.jpg](https://commons.wikimedia.org/wiki/File:Damaged_piston_from_a_Subaru_Impreza_boxer_engine-2011.jpg)，Phil Sangwell，CC BY 2.0 |
| IMG004 | `broken_piston_connecting_rod.jpg` | 故障样本 | 踏板车断裂的活塞与连杆；可识别活塞、连杆和断裂 | [Failed piston and connecting rod.jpg](https://commons.wikimedia.org/wiki/File:Failed_piston_and_connecting_rod.jpg)，Kallemax，Public Domain |
| IMG005 | `gear_tooth_fatigue_fracture.jpg` | 跨设备泛化样本 | 中间齿轮齿部内部疲劳断裂；可识别齿轮和断齿/疲劳断裂 | [Tooth Interior Fatigue Fracture 1.jpg](https://commons.wikimedia.org/wiki/File:Tooth_Interior_Fatigue_Fracture_1.jpg)，Mackaldener，Public Domain |
| IMG006 | `bearing_severe_spalling.jpg` | 跨设备泛化样本 | 轴承套圈严重剥落；可识别轴承和表面剥落 | [Ecaillage severe sur une bague de roulement.jpg](https://commons.wikimedia.org/wiki/File:Ecaillage_severe_sur_une_bague_de_roulement.jpg)，Jean-Jacques MILAN，CC BY-SA 3.0 / GFDL |

## 保存位置

将图片保存到：

`data/raw/fault-images/`

该目录位于 `data/raw/` 下，默认不应提交到 Git；清单可以提交。

## 建议测试问题

1. 图中是什么部件？是否存在明显异常？
2. 请提取图片中可见的型号、文字或告警码。
3. 根据图片给出疑似故障，但明确区分“可见事实”和“推测原因”。
4. 检索知识库中与该部件和故障现象相关的手册章节。
5. 返回检查步骤、处理建议、安全提醒及手册页码引用。

## 判定注意事项

- 图片标签只作为初步测试真值，不能替代专业人员诊断。
- 系统不应仅凭图片断言不可见的故障原因。
- 正常火花塞样本用于测试模型是否会过度诊断。
- 齿轮和轴承样本用于验证系统能否处理手册范围外的图片，并给出“知识库证据不足”。
