import logging
from users.audit import record_system_audit_event

from django.core.cache import cache

logger = logging.getLogger(__name__)

RANKING_CACHE_TTL = 60  # 60-second in-memory TTL with event-driven invalidation


class RankingService:
    """
    Focused domain service for institutional Class Index calculations,
    snapshot persistence, and historical rankings management.
    """

    @staticmethod
    def get_cache_key(year=None, explain=False):
        safe_year = str(year).strip() if year else 'all'
        return f"class_index_data:{safe_year}:{'exp' if explain else 'std'}"

    @staticmethod
    def invalidate_cache(year=None):
        """Invalidates cached ranking calculations across all variants."""
        keys = [
            RankingService.get_cache_key(year, False),
            RankingService.get_cache_key(year, True),
            RankingService.get_cache_key(None, False),
            RankingService.get_cache_key(None, True),
        ]
        if year:
            # Also clear active year string variants
            keys.append(RankingService.get_cache_key('2025-2026', False))
            keys.append(RankingService.get_cache_key('2025-2026', True))
        for k in set(keys):
            cache.delete(k)
        logger.debug(f"Invalidated ranking cache for year='{year}'")

    @staticmethod
    def get_class_index_data(year=None, explain=False):
        """
        Retrieves either historical locked snapshots or dynamic live calculations
        for the given academic year.
        Utilizes in-process caching with event-driven invalidation to avoid redundant recalculation.
        """
        # Check in-process cache first
        cache_key = RankingService.get_cache_key(year, explain)
        cached_data = cache.get(cache_key)
        if cached_data is not None:
            return cached_data

        # 1. Historical Snapshot: If academic year has published locked results, serve directly from ClassIndexResult
        if year:
            from users.models import AcademicYear, ClassIndexResult
            ay = AcademicYear.objects.filter(year=year).first()
            if ay:
                locked_snaps = ClassIndexResult.objects.filter(academic_year=ay, is_locked=True).select_related(
                    'class_name', 'class_name__department'
                )
                if locked_snaps.exists():
                    snap_ranked = []
                    snap_unranked = []
                    for snap in locked_snaps:
                        if snap.snapshot_data:
                            entry = dict(snap.snapshot_data)
                        else:
                            entry = {
                                "class_id": snap.class_name.id,
                                "class_name": snap.class_name.name,
                                "department": snap.class_name.department.name if snap.class_name.department else 'General',
                                "department_code": snap.class_name.department.code if snap.class_name.department else 'GEN',
                                "N": snap.class_name.num_students,
                                "M": snap.final_index,
                                "rank": snap.rank,
                                "academic_score": snap.academic_score,
                                "co_curricular_score": snap.co_curricular_score,
                                "extra_curricular_score": snap.extra_curricular_score,
                                "scoring_version": snap.scoring_version,
                                "is_locked": snap.is_locked,
                            }
                        if snap.rank is not None:
                            snap_ranked.append(entry)
                        else:
                            snap_unranked.append(entry)
                    snap_ranked.sort(key=lambda x: (-(x["M"] if x["M"] is not None else -1.0), x["class_name"].lower() if x.get("class_name") else ""))
                    res = snap_ranked + snap_unranked
                    cache.set(cache_key, res, RANKING_CACHE_TTL)
                    return res

        # 2. Dynamic Live Calculation
        from users.scoring_engine import compute_all_rankings, explain_class_score
        if explain:
            from users.models import Class
            all_classes = Class.objects.select_related('department').all()
            explanations = [explain_class_score(cls, academic_year=year) for cls in all_classes]
            ranked_exp = [e for e in explanations if e["N"] > 0]
            unranked_exp = [e for e in explanations if e["N"] == 0]
            ranked_exp.sort(key=lambda x: (
                -(x["M"] if x["M"] is not None else -1.0),
                x["class_name"].lower() if x["class_name"] else ""
            ))
            for idx, item in enumerate(ranked_exp):
                if idx > 0:
                    prev = ranked_exp[idx - 1]
                    prev_m = prev["M"] if prev["M"] is not None else 0.0
                    curr_m = item["M"] if item["M"] is not None else 0.0
                    if abs(curr_m - prev_m) < 1e-5:
                        item["rank"] = prev["rank"]
                    else:
                        item["rank"] = idx + 1
                else:
                    item["rank"] = 1
            for item in unranked_exp:
                item["rank"] = None
            res = ranked_exp + unranked_exp
            cache.set(cache_key, res, RANKING_CACHE_TTL)
            return res

        ranked, unranked = compute_all_rankings(academic_year=year)
        res = ranked + unranked
        cache.set(cache_key, res, RANKING_CACHE_TTL)
        return res

    @staticmethod
    def snapshot_rankings(year, user, force=False, lock=True, request=None):
        """
        Executes official snapshot and records system audit event.
        """
        from users.scoring_engine import snapshot_academic_year_results
        results = snapshot_academic_year_results(year, force=force, mark_locked=lock)

        record_system_audit_event(
            action='RANKING_PUBLISH' if lock else 'RANKING_CALCULATE',
            object_type='AcademicYear',
            object_id=year,
            actor=user,
            object_repr=f"Official Rankings for Academic Year {year} ({len(results)} classes, locked={lock})",
            old_value=None,
            new_value={'academic_year': year, 'class_count': len(results), 'is_locked': lock},
            reason=f"Rankings {'published and locked' if lock else 'calculated and snapshotted'} by {getattr(user, 'email', '')}",
            request=request
        )

        # Invalidate cache after official snapshot/publish
        RankingService.invalidate_cache(year)

        return results
