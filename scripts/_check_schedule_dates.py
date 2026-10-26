from app.xttv_schedule_import import schedule_entries_for_lid

e = schedule_entries_for_lid(8817, 26, limit=500)
with_dates = sum(1 for _, d in e if d)
print("entries", len(e), "with_dates", with_dates)
for meid, d in e:
    if meid == 462244:
        print("462244 date", d)
        break
else:
    print("462244 not in list")
