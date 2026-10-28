# 设计 add-tag

技术方案：tags 作为条目内的可选数组字段，读取端 `setdefault("tags", [])` 兜底——旧 store.json 无该字段不炸。
去重放在写入口（add_tag 单点判断），读端不做第二次过滤（单一真相源：去重语义只实现一处）。
