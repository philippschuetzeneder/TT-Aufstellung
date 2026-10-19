from app.xttv_db_import import _max_imported_meid, _get_scan_frontier, _resolve_scan_frontier

l = _max_imported_meid()
print("last_imported_meid", l)
print("frontier_raw", _get_scan_frontier())
print("frontier_resolved", _resolve_scan_frontier(l))
