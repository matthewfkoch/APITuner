/*
 * Foreground service hosting the Agent HTTP server + mDNS advertisement.
 * Derived from DisplayLauncher's LauncherService (Apache-2.0):
 *   https://github.com/mouldybread/DisplayLauncher
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
import com.apituner.agent.util.UpdateChecker
import com.apituner.agent.web.AgentWebServer
import java.util.concurrent.Executors

class AgentService : Service() {

    private var webServer: AgentWebServer? = null
    private var nsdManager: NsdManager? = null
    private var registrationListener: NsdManager.RegistrationListener? = null
    private var monitoringStarted = false
    private val tag = "AgentService"
    private val handler = Handler(Looper.getMainLooper())
    private val updateExecutor = Executors.newSingleThreadExecutor()
    private val appLauncher by lazy { AppLauncher(applicationContext) }
    private val pendingStartRunnable = Runnable {
        startWebServer()
        registerService()
        startMonitoring()
        scheduleUpdateCheck(delayMs = 30_000)
    }
    private val updateCheckRunnable = Runnable { runUpdateCheck() }
    private val monitorRunnable = object : Runnable {
        override fun run() {
            if (webServer == null || webServer?.isAlive != true) {
                Log.w(tag, "Web server down; restarting")
                startWebServer()
            }
            if (AgentPrefs.shouldAutoCheckNow(applicationContext)) {
                runUpdateCheck()
            }
            handler.postDelayed(this, 60_000)
        }
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        createNotificationChannel()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        startForegroundNotification()
        val delayMs = (intent?.getLongExtra(EXTRA_START_DELAY_MS, 0L) ?: 0L).coerceAtLeast(0L)
        handler.removeCallbacks(pendingStartRunnable)
        if (delayMs > 0L && webServer?.isAlive != true) {
            Log.i(tag, "Deferring Agent HTTP/mDNS start by ${delayMs}ms")
            handler.postDelayed(pendingStartRunnable, delayMs)
        } else {
            pendingStartRunnable.run()
        }
        return START_STICKY
    }

    override fun onTaskRemoved(rootIntent: Intent?) {
        // Keep the control server running if the user leaves the settings UI.
        super.onTaskRemoved(rootIntent)
        AgentService.start(applicationContext)
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
        if (webServer?.isAlive == true) return
        try {
            webServer?.stop()
            webServer = AgentWebServer(
                AgentPrefs.DEFAULT_PORT, applicationContext, appLauncher
            )
            webServer?.start(NanoTimeouts.SOCKET_READ_TIMEOUT, false)
            Log.d(tag, "Web server started on ${AgentPrefs.DEFAULT_PORT}")
        } catch (e: Exception) {
            Log.e(tag, "Failed to start web server: ${e.message}", e)
        }
    }

    private fun unregisterService() {
        val listener = registrationListener ?: return
        registrationListener = null
        try {
            nsdManager?.unregisterService(listener)
        } catch (_: Exception) {
        }
    }

    private fun registerService() {
        if (registrationListener != null) return
        try {
            nsdManager = getSystemService(Context.NSD_SERVICE) as NsdManager
            val info = NsdServiceInfo().apply {
                serviceName = "APITuner Agent (${Build.MODEL})"
                serviceType = "_apituner._tcp."
                port = AgentPrefs.DEFAULT_PORT
            }
            val listener = object : NsdManager.RegistrationListener {
                override fun onServiceRegistered(info: NsdServiceInfo) {
                    Log.d(tag, "mDNS registered: ${info.serviceName}")
                }
                override fun onRegistrationFailed(info: NsdServiceInfo, errorCode: Int) {
                    Log.w(tag, "mDNS registration failed: $errorCode")
                    if (registrationListener === this) {
                        registrationListener = null
                    }
                }
                override fun onServiceUnregistered(info: NsdServiceInfo) {}
                override fun onUnregistrationFailed(info: NsdServiceInfo, errorCode: Int) {}
            }
            registrationListener = listener
            nsdManager?.registerService(
                info, NsdManager.PROTOCOL_DNS_SD, listener
            )
        } catch (e: Exception) {
            registrationListener = null
            Log.w(tag, "mDNS registration error: ${e.message}")
        }
    }

    private fun startMonitoring() {
        if (monitoringStarted) return
        monitoringStarted = true
        handler.removeCallbacks(monitorRunnable)
        handler.postDelayed(monitorRunnable, 60_000)
    }

    private fun scheduleUpdateCheck(delayMs: Long) {
        handler.removeCallbacks(updateCheckRunnable)
        handler.postDelayed(updateCheckRunnable, delayMs)
    }

    private fun runUpdateCheck() {
        if (!AgentPrefs.shouldAutoCheckNow(applicationContext)) return
        if (UpdateChecker.isBusy()) return
        updateExecutor.execute {
            try {
                val result = UpdateChecker(applicationContext, appLauncher)
                    .checkAndInstallIfNewer(force = false)
                AgentPrefs.setLastUpdateCheckMs(applicationContext, System.currentTimeMillis())
                when (result) {
                    is UpdateChecker.CheckResult.InstallStarted ->
                        Log.i(tag, "Agent update install dialog opened for ${result.latest.versionName}")
                    is UpdateChecker.CheckResult.UpdateAvailable ->
                        Log.i(tag, "Agent update available: ${result.latest.versionName}")
                    is UpdateChecker.CheckResult.UpToDate ->
                        Log.d(tag, "Agent up to date (${result.current.versionName})")
                    is UpdateChecker.CheckResult.Skipped ->
                        Log.d(tag, "Agent update skipped: ${result.reason}")
                    is UpdateChecker.CheckResult.Failed ->
                        Log.w(tag, "Agent update check failed: ${result.message}")
                }
            } catch (e: Exception) {
                Log.w(tag, "Agent update check error: ${e.message}")
            }
        }
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
        monitoringStarted = false
        try { webServer?.stop() } catch (_: Exception) {}
        webServer = null
        unregisterService()
        handler.removeCallbacksAndMessages(null)
        updateExecutor.shutdownNow()
    }

    private object NanoTimeouts {
        const val SOCKET_READ_TIMEOUT = 10_000
    }

    companion object {
        const val NOTIFICATION_ID = 1
        const val CHANNEL_ID = "ApiTunerAgentChannel"
        const val EXTRA_START_DELAY_MS = "com.apituner.agent.EXTRA_START_DELAY_MS"

        fun start(context: Context, startDelayMs: Long = 0L) {
            val intent = Intent(context, AgentService::class.java).apply {
                if (startDelayMs > 0L) {
                    putExtra(EXTRA_START_DELAY_MS, startDelayMs)
                }
            }
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                context.startForegroundService(intent)
            } else {
                context.startService(intent)
            }
        }
    }
}
