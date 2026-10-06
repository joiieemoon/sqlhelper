import json
from pathlib import Path
root=Path(r"c:\Team G\Jainil K task SQLopt\backend\postman")
for name in ["SQL-Optimizer.postman_collection.json","SQL-Optimizer-Local.postman_environment.json"]:
    p=root/name
    try:
        data=json.loads(p.read_text(encoding="utf-8-sig"))
        print(name, "OK", "top-level", list(data)[:8])
        if name.endswith("collection.json"):
            for folder in data.get("item",[]):
                print("FOLDER", folder.get("name"), "items", len(folder.get("item",[])))
                for item in folder.get("item",[]):
                    if "db_optimize_full" in str(item.get("request",{}).get("url")):
                        raw=item.get("request",{}).get("body",{}).get("raw","")
                        try:
                            body=json.loads(raw)
                            print("REQUEST",item.get("name"),"RAW_JSON_OK",body)
                        except Exception as e:
                            print("REQUEST",item.get("name"),"RAW_JSON_FAIL",repr(e),repr(raw))
    except Exception as e:
        print(name,"FILE_FAIL",repr(e))
