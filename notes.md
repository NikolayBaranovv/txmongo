## Currently supported versions:
- Python: 3.8+
- MongoDB: 5.0+ (actually 6.0+ soon)
    - new `OP_MSG` protocol is supported in MongoDB 3.6+
- PyMongo: probably 3.12+
    - PyMongo 3.x still supports Py2.7 and MongoDB 2.6+
    - PyMongo 3.8 doesn't have `bson.encode/decode` functions

## TODO
- Check what are the other packages listed in tox.ini. Do we really need them?
  - Can the pinning versions of these packages reduce tox startup time?
- Do we need OP_COMPRESSED?


## Plan:
- Запускать тесты в Github Actions против разных версий pymongo
- Запускать тесты в Github Actions против разных версий twisted
- Добавить в тесты pymongo 3.12, 3.13, и пару последних веток 4.x
- Починить остальные несовместимости с pymongo 4
- Реализовать поддержку OP_MSG-протокола (*)
- Выпилить все старые протоколы, кроме OP_MSG (*)
- Добавить тесты против MongoDB 5.0+ (*)
- Дропнуть совместимость с MongoDB <3.6 (*)

Пункты, отмеченные звёздочками проще всего сделать одним куском, а не последовательно.
Иначе придётся усложнять код поддержкой нескольких протоколов.