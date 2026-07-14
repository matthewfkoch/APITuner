package com.apituner.agent.util

import android.content.Context

object AgentPrefs {
    private const val PREFS = "apituner_agent"
    private const val KEY_TOKEN = "auth_token"
    private const val KEY_AUTO_UPDATE = "auto_update"
    private const val KEY_LAST_UPDATE_CHECK_MS = "last_update_check_ms"
    const val DEFAULT_PORT = 9092
    const val UPDATE_CHECK_INTERVAL_MS = 24L * 60 * 60 * 1000

    fun getToken(context: Context): String =
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .getString(KEY_TOKEN, "") ?: ""

    fun setToken(context: Context, token: String) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .edit().putString(KEY_TOKEN, token).apply()
    }

    fun isAutoUpdateEnabled(context: Context): Boolean =
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .getBoolean(KEY_AUTO_UPDATE, true)

    fun setAutoUpdateEnabled(context: Context, enabled: Boolean) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .edit().putBoolean(KEY_AUTO_UPDATE, enabled).apply()
    }

    fun getLastUpdateCheckMs(context: Context): Long =
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .getLong(KEY_LAST_UPDATE_CHECK_MS, 0L)

    fun setLastUpdateCheckMs(context: Context, whenMs: Long) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .edit().putLong(KEY_LAST_UPDATE_CHECK_MS, whenMs).apply()
    }

    fun shouldAutoCheckNow(context: Context, nowMs: Long = System.currentTimeMillis()): Boolean {
        if (!isAutoUpdateEnabled(context)) return false
        val last = getLastUpdateCheckMs(context)
        return last == 0L || nowMs - last >= UPDATE_CHECK_INTERVAL_MS
    }
}
