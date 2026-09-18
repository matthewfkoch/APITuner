package com.apituner.agent.control

import android.content.ComponentName
import android.content.Context
import android.media.MediaMetadata
import android.media.session.MediaSessionManager
import android.media.session.PlaybackState
import android.provider.Settings

data class PlaybackSnapshot(
    val playing: Boolean?,
    val packageName: String?,
    val title: String? = null,
    val state: String? = null,
)

/** Reads media playback state via active MediaSessions (needs Notification access). */
class PlaybackDetector(private val context: Context) {

    private val listenerComponent =
        ComponentName(context, PlaybackNotificationListener::class.java)

    fun hasPermission(): Boolean {
        return try {
            val enabled = Settings.Secure.getString(
                context.contentResolver, "enabled_notification_listeners"
            ) ?: return false
            enabled.split(":").any { it.contains(context.packageName) }
        } catch (e: Exception) {
            false
        }
    }

    /** Returns Pair(isPlaying, packageName-of-active-session) or (null, null) on failure. */
    fun playbackState(): Pair<Boolean?, String?> {
        val snap = snapshot()
        return snap.playing to snap.packageName
    }

    fun snapshot(): PlaybackSnapshot {
        return try {
            val msm =
                context.getSystemService(Context.MEDIA_SESSION_SERVICE) as MediaSessionManager
            val sessions = msm.getActiveSessions(listenerComponent)
            for (controller in sessions) {
                val state = controller.playbackState ?: continue
                if (state.state == PlaybackState.STATE_PLAYING ||
                    state.state == PlaybackState.STATE_BUFFERING
                ) {
                    return PlaybackSnapshot(
                        playing = true,
                        packageName = controller.packageName,
                        title = sessionTitle(controller),
                        state = stateName(state.state),
                    )
                }
            }
            val first = sessions.firstOrNull()
            val paused = first?.playbackState
            PlaybackSnapshot(
                playing = false,
                packageName = first?.packageName,
                title = first?.let { sessionTitle(it) },
                state = paused?.state?.let { stateName(it) },
            )
        } catch (e: SecurityException) {
            PlaybackSnapshot(null, null)
        } catch (e: Exception) {
            PlaybackSnapshot(null, null)
        }
    }

    private fun sessionTitle(controller: android.media.session.MediaController): String? {
        val meta = controller.metadata ?: return null
        return listOf(
            meta.getString(MediaMetadata.METADATA_KEY_DISPLAY_TITLE),
            meta.getString(MediaMetadata.METADATA_KEY_TITLE),
            meta.getString(MediaMetadata.METADATA_KEY_DISPLAY_SUBTITLE),
        ).firstOrNull { !it.isNullOrBlank() }
    }

    private fun stateName(state: Int): String = when (state) {
        PlaybackState.STATE_PLAYING -> "playing"
        PlaybackState.STATE_BUFFERING -> "buffering"
        PlaybackState.STATE_PAUSED -> "paused"
        PlaybackState.STATE_STOPPED -> "stopped"
        PlaybackState.STATE_NONE -> "none"
        else -> "other"
    }
}
