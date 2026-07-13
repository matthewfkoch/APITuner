/*
 * Foreground service hosting the Agent HTTP server + mDNS advertisement.
 * Derived from DisplayLauncher's LauncherService (Apache-2.0).
 */
package com.apituner.agent

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.net.nsd.NsdManager
import android.net.nsd.NsdServiceInfo
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.util.Log
import androidx.core.app.NotificationCompat
import com.apituner.agent.control.AppLauncher
import com.apituner.agent.util.AgentPrefs
import com.apituner.agent.web.AgentWebServer

class AgentService : Service() {

    private var webServer: AgentWebServer? = null
    private var nsdManager: NsdManager? = null
    private var registrationListener: NsdManager.RegistrationListener? = null
    private val tag = "AgentService"
    private val handler = Handler(Looper.getMainLooper())

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        createNotificationChannel()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        startForegroundNotification()
        startWebServer()
        registerService()
        startMonitoring()
        return START_STICKY
    }

    private fun startForegroundNotification() {
        val pi = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE
        )
        val notification = NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("APITuner Agent")
            .setContentText("HTTP control server running on port ${AgentPrefs.DEFAULT_PORT}")
            .setSmallIcon(android.R.drawable.ic_menu_manage)
            .setContentIntent(pi)
            .setOngoing(true)
            .build()
        startForeground(NOTIFICATION_ID, notification)
    }

    private fun startWebServer() {
        try {
            webServer?.stop()
            webServer = AgentWebServer(
                AgentPrefs.DEFAULT_PORT, applicationContext, AppLauncher(applicationContext)
            )
            webServer?.start(NanoTimeouts.SOCKET_READ_TIMEOUT, false)
            Log.d(tag, "Web server started on ${AgentPrefs.DEFAULT_PORT}")
        } catch (e: Exception) {
            Log.e(tag, "Failed to start web server: ${e.message}", e)
        }
    }

    private fun registerService() {
        try {
            nsdManager = getSystemService(Context.NSD_SERVICE) as NsdManager
            val info = NsdServiceInfo().apply {
                serviceName = "APITuner Agent (${Build.MODEL})"
                serviceType = "_apituner._tcp."
                port = AgentPrefs.DEFAULT_PORT
            }
            registrationListener = object : NsdManager.RegistrationListener {
                override fun onServiceRegistered(info: NsdServiceInfo) {
                    Log.d(tag, "mDNS registered: ${info.serviceName}")
                }
                override fun onRegistrationFailed(info: NsdServiceInfo, errorCode: Int) {
                    Log.w(tag, "mDNS registration failed: $errorCode")
                }
                override fun onServiceUnregistered(info: NsdServiceInfo) {}
                override fun onUnregistrationFailed(info: NsdServiceInfo, errorCode: Int) {}
            }
            nsdManager?.registerService(
                info, NsdManager.PROTOCOL_DNS_SD, registrationListener
            )
        } catch (e: Exception) {
            Log.w(tag, "mDNS registration error: ${e.message}")
        }
    }

    private fun startMonitoring() {
        handler.postDelayed(object : Runnable {
            override fun run() {
                if (webServer == null || webServer?.isAlive != true) {
                    Log.w(tag, "Web server down; restarting")
                    startWebServer()
                }
                handler.postDelayed(this, 60_000)
            }
        }, 60_000)
    }

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                CHANNEL_ID, "APITuner Agent Service", NotificationManager.IMPORTANCE_LOW
            ).apply {
                description = "Keeps the control server running"
                setShowBadge(false)
            }
            getSystemService(NotificationManager::class.java)?.createNotificationChannel(channel)
        }
    }

    override fun onDestroy() {
        super.onDestroy()
        try { webServer?.stop() } catch (_: Exception) {}
        try {
            registrationListener?.let { nsdManager?.unregisterService(it) }
        } catch (_: Exception) {}
        handler.removeCallbacksAndMessages(null)
    }

    private object NanoTimeouts {
        const val SOCKET_READ_TIMEOUT = 10_000
    }

    companion object {
        const val NOTIFICATION_ID = 1
        const val CHANNEL_ID = "ApiTunerAgentChannel"

        fun start(context: Context) {
            val intent = Intent(context, AgentService::class.java)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                context.startForegroundService(intent)
            } else {
                context.startService(intent)
            }
        }
    }
}
