---
title: Travel Compliance Desk
emoji: 🧳
colorFrom: indigo
colorTo: blue
sdk: docker
app_port: 7860
pinned: false
---

# Travel Compliance Desk · 差旅合规工作台

一个面向 FDE（方案交付）场景的差旅合规与审批工作台演示：员工提交差旅申请后，系统用 LLM 抽取行程要素、对照差旅制度自动生成合规判定，超标的申请进入人工审批流（LangGraph 工作流 + 中断/恢复），支持 SSE 实时进度、附件 OCR、制度问答（RAG）与费用报表。

**演示身份**（页面顶部"切换身份"可随时切换，无需注册）：

- 张三（员工）：创建差旅申请、上传会议通知、查看合规结果
- 李四 / 赵六（审批经理）：审批中心处理超标申请
- 王五（管理员）：制度管理、运维面板、报表分析

> 说明：这是演示环境，数据为虚构的示例数据；Space 重启后数据会重置为初始演示状态。
