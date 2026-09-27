package com.apituner.agent.control

import android.app.AppOpsManager
import android.app.usage.UsageEvents
import android.app.usage.UsageStatsManager
import android.content.Context
import android.os.Build
import android.os.Process

/** Reads the foreground app via UsageStats (requires the Usage Access permission). */
class ForegroundAppDetector(private val context: Context) {

    private var cachedPackage: String? = null
    private var cachedAtMs: Long = 0L
    private var cacheFresh: Boolean = false

    fun hasPermission(): Boolean {
        return try {
            val appOps = context.getSystemService(Context.APP_OPS_SERVICE) as AppOpsManager
            // unsafeCheckOpNoThrow is API 29+; Fire OS 7 / Android 9 (API 28) only has checkOpNoThrow.
            @Suppress("DEPRECATION")
            val mode = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                appOps.unsafeCheckOpNoThrow(
                    AppOpsManager.OPSTR_GET_USAGE_STATS,
                    Process.myUid(),
                    context.packageName
                )
            } else {
                appOps.checkOpNoThrow(
                    AppOpsManager.OPSTR_GET_USAGE_STATS,
                    Process.myUid(),
                    context.packageName
                )
            }
            mode == AppOpsManager.MODE_ALLOWED
        } catch (e: Throwable) {
            false
        }
    }

    @Synchronized
    fun currentForegroundPackage(): String? {
        if (!hasPermission()) return null
        val now = System.currentTimeMillis()
        if (cacheFresh && now - cachedAtMs < REFRESH_MS) {
            return cachedPackage
        }
        val resolved = when (val events = fromUsageEvents(now)) {
            is EventRead.Package -> events.name
            EventRead.Cleared -> null
            EventRead.None -> fromUsageStats(now) ?: cachedPackage
        }
        cachedPackage = resolved
        cachedAtMs = now
        cacheFresh = true
        return resolved
    }

    /**
     * Newest resume in the lookback wins. A later background or pause of that
     * same package clears it, so the launcher is not reported as the app that
     * just left. No relevant events means the caller should try usage stats.
     */
    private fun fromUsageEvents(now: Long): EventRead {
        return try {
            val usm = context.getSystemService(Context.USAGE_STATS_SERVICE) as UsageStatsManager
            val events = usm.queryEvents(now - LOOKBACK_MS, now)
            val event = UsageEvents.Event()
            var saw = false
            var current: String? = null
            while (events.hasNextEvent()) {
                events.getNextEvent(event)
                val pkg = event.packageName
                if (pkg.isNullOrBlank()) continue
                when (event.eventType) {
                    EVENT_MOVE_TO_FOREGROUND, EVENT_ACTIVITY_RESUMED -> {
                        saw = true
                        current = pkg
                    }
                    EVENT_MOVE_TO_BACKGROUND, EVENT_ACTIVITY_PAUSED -> {
                        saw = true
                        if (current == null || current == pkg) current = null
                    }
                }
            }
            val found = current
            when {
                !saw -> EventRead.None
                found != null -> EventRead.Package(found)
                else -> EventRead.Cleared
            }
        } catch (e: Exception) {
            EventRead.None
        }
    }

    /** Fallback when playback does not emit a fresh foreground event. */
    private fun fromUsageStats(now: Long): String? {
        return try {
            val usm = context.getSystemService(Context.USAGE_STATS_SERVICE) as UsageStatsManager
            val stats = usm.queryUsageStats(
                UsageStatsManager.INTERVAL_BEST,
                now - LOOKBACK_MS,
                now,
            ) ?: return null
            stats
                .filter { it.lastTimeUsed > now - LOOKBACK_MS && !it.packageName.isNullOrBlank() }
                .maxByOrNull { it.lastTimeUsed }
                ?.packageName
        } catch (e: Exception) {
            null
        }
    }

    private sealed class EventRead {
        data class Package(val name: String) : EventRead()
        data object Cleared : EventRead()
        data object None : EventRead()
    }

    companion object {
        private const val REFRESH_MS = 1_000L
        /** Long enough that a show already playing is still the foreground app. */
        private const val LOOKBACK_MS = 6L * 60L * 60L * 1000L

        // Int literals so API 23 does not crash on fields added in API 29.
        private const val EVENT_MOVE_TO_FOREGROUND = 1
        private const val EVENT_MOVE_TO_BACKGROUND = 2
        private const val EVENT_ACTIVITY_RESUMED = 19
        private const val EVENT_ACTIVITY_PAUSED = 20
    }
}
