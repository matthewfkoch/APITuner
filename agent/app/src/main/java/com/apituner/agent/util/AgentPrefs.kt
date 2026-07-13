package com.apituner.agent.util

import android.content.Context

object AgentPrefs {
    private const val PREFS = "apituner_agent"
    private const val KEY_TOKEN = "auth_token"
    const val DEFAULT_PORT = 9092

    fun getToken(context: Context): String =
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .getString(KEY_TOKEN, "") ?: ""

    fun setToken(context: Context, token: String) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .edit().putString(KEY_TOKEN, token).apply()
    }
}
