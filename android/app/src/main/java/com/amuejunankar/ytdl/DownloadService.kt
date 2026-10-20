package com.amuejunankar.ytdl

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat
import com.yausername.ffmpeg.FFmpeg
import com.yausername.youtubedl_android.YoutubeDL
import com.yausername.youtubedl_android.YoutubeDLRequest
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import java.io.File

class DownloadService : Service() {

    companion object {
        const val ACTION_START = "com.amuejunankar.ytdl.START"
        const val ACTION_CANCEL = "com.amuejunankar.ytdl.CANCEL"

        const val EXTRA_URL = "url"
        const val EXTRA_FORMAT = "format"
        const val EXTRA_AUDIO = "audio"
        const val EXTRA_PLAYLIST = "playlist"

        private const val CHANNEL_ID = "downloads"
        private const val NOTIFICATION_ID = 1
        private const val PROCESS_ID = "active-download"

        private val MEDIA_EXTENSIONS = setOf(
            "mp4", "mkv", "webm", "m4v", "mov", "avi",
            "mp3", "m4a", "opus", "ogg", "aac", "flac", "wav",
        )

        fun start(context: Context, url: String, format: String, audio: Boolean, playlist: Boolean) {
            val intent = Intent(context, DownloadService::class.java).apply {
                action = ACTION_START
                putExtra(EXTRA_URL, url)
                putExtra(EXTRA_FORMAT, format)
                putExtra(EXTRA_AUDIO, audio)
                putExtra(EXTRA_PLAYLIST, playlist)
            }
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                context.startForegroundService(intent)
            } else {
                context.startService(intent)
            }
        }

        fun cancel(context: Context) {
            val intent = Intent(context, DownloadService::class.java).apply { action = ACTION_CANCEL }
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                context.startForegroundService(intent)
            } else {
                context.startService(intent)
            }
        }
    }

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private var job: Job? = null
    private var lastNotified = -1
    private var cancelRequested = false

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        createChannel()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val action = intent?.action
        if (action == ACTION_CANCEL) {
            startForeground(NOTIFICATION_ID, buildNotification(getString(R.string.cancelled), -1, false))
            cancelRequested = true
            runCatching { YoutubeDL.getInstance().destroyProcessById(PROCESS_ID) }
            job?.cancel()
            DownloadBus.status.value = DownloadStatus(message = getString(R.string.cancelled))
            postFinalNotification(getString(R.string.cancelled))
            stopForeground(STOP_FOREGROUND_REMOVE)
            stopSelf()
            return START_NOT_STICKY
        }

        val url = intent?.getStringExtra(EXTRA_URL) ?: return START_NOT_STICKY
        val format = intent.getStringExtra(EXTRA_FORMAT) ?: "bv*+ba/b"
        val audio = intent.getBooleanExtra(EXTRA_AUDIO, false)
        val playlist = intent.getBooleanExtra(EXTRA_PLAYLIST, false)

        cancelRequested = false
        startForeground(NOTIFICATION_ID, buildNotification(getString(R.string.downloading), -1, true))
        runDownload(url, format, audio, playlist)
        return START_NOT_STICKY
    }

    private fun runDownload(url: String, format: String, audio: Boolean, playlist: Boolean) {
        job?.cancel()
        job = scope.launch {
            DownloadBus.status.value = DownloadStatus(running = true, message = getString(R.string.downloading))
            val tempDir = File(cacheDir, "dl").apply { mkdirs() }
            runCatching { tempDir.listFiles()?.forEach { it.deleteRecursively() } }

            try {
                ensureEngineReady()

                val request = YoutubeDLRequest(url)
                if (audio) {
                    request.addOption("-f", "bestaudio/best")
                    request.addOption("-x")
                    request.addOption("--audio-format", "mp3")
                    request.addOption("--audio-quality", "0")
                } else {
                    request.addOption("-f", format)
                    request.addOption("--merge-output-format", "mp4/mkv")
                }
                request.addOption(if (playlist) "--yes-playlist" else "--no-playlist")
                if (playlist) request.addOption("--ignore-errors")
                request.addOption("--retries", "10")
                request.addOption("--fragment-retries", "10")
                request.addOption("--concurrent-fragments", "4")
                request.addOption("--socket-timeout", "30")
                request.addOption("--no-mtime")
                request.addOption("--newline")
                request.addOption("-P", tempDir.absolutePath)
                request.addOption("-o", outputTemplate(playlist))

                val callback: (Float, Long, String) -> Unit = { progress, eta, line ->
                    if (!cancelRequested) {
                        DownloadBus.status.value = DownloadBus.status.value.copy(
                            running = true,
                            progress = progress,
                            etaSeconds = eta,
                            message = line.takeIf { it.isNotBlank() } ?: getString(R.string.downloading),
                        )
                        notifyProgress(progress)
                    }
                }

                YoutubeDL.getInstance().execute(request, PROCESS_ID, callback)
                if (cancelRequested) throw CancellationException("cancelled")

                val produced = tempDir.walkTopDown()
                    .filter { it.isFile && it.length() > 0 && it.extension.lowercase() in MEDIA_EXTENSIONS }
                    .toList()

                if (produced.isEmpty()) {
                    throw IllegalStateException("No media file was produced")
                }

                val failures = mutableListOf<String>()
                produced.forEach { file ->
                    runCatching { MediaStoreSaver.save(applicationContext, file, audio) }
                        .onFailure { failures += "${file.name}: ${it.message}" }
                }
                produced.forEach { runCatching { it.delete() } }

                if (failures.size == produced.size) {
                    throw IllegalStateException(failures.first())
                }

                val where = if (audio) "Music/YouTube Downloader" else "Movies/YouTube Downloader"
                DownloadBus.status.value = DownloadStatus(
                    finished = true,
                    progress = 100f,
                    message = getString(R.string.downloaded, where),
                    result = where,
                )
                postFinalNotification(getString(R.string.downloaded, where))
            } catch (e: CancellationException) {
                if (!cancelRequested) {
                    DownloadBus.status.value = DownloadStatus(error = getString(R.string.cancelled))
                }
                postFinalNotification(getString(R.string.cancelled))
            } catch (e: YoutubeDL.CanceledException) {
                DownloadBus.status.value = DownloadStatus(message = getString(R.string.cancelled))
                postFinalNotification(getString(R.string.cancelled))
            } catch (t: Throwable) {
                val message = friendly(t.message)
                DownloadBus.status.value = DownloadStatus(error = message)
                postFinalNotification(getString(R.string.notification_failed))
            } finally {
                stopForeground(STOP_FOREGROUND_DETACH)
                stopSelf()
            }
        }
    }

    private fun ensureEngineReady() {
        val app = applicationContext
        runCatching { YoutubeDL.getInstance().init(app) }
        runCatching { FFmpeg.getInstance().init(app) }
    }

    private fun outputTemplate(playlist: Boolean): String = if (playlist) {
        "%(playlist_title).100B/%(playlist_index)03d - %(title).150B.%(ext)s"
    } else {
        "%(title).150B [%(id)s].%(ext)s"
    }

    private fun friendly(raw: String?): String {
        val text = raw?.replace(Regex("\u001B\\[[;\\d]*m"), "")?.trim()
        if (text.isNullOrBlank()) return getString(R.string.failed)
        val lines = text.lineSequence().map { it.trim() }.filter { it.isNotBlank() }.toList()
        val errorLine = lines.lastOrNull { it.startsWith("ERROR:", ignoreCase = true) }
            ?: lines.lastOrNull { !it.contains("% of") && !it.contains("ETA") }
            ?: lines.last()
        return errorLine.removePrefix("ERROR:").trim().take(300).ifBlank { getString(R.string.failed) }
    }

    private fun notifyProgress(progress: Float) {
        val pct = if (progress >= 0f) progress.toInt().coerceIn(0, 100) else -1
        if (pct >= 0 && pct < lastNotified + 2 && pct < 100) return
        lastNotified = pct
        val nm = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        nm.notify(NOTIFICATION_ID, buildNotification(getString(R.string.downloading), pct, true))
    }

    private fun postFinalNotification(text: String) {
        val nm = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        nm.notify(NOTIFICATION_ID, buildNotification(text, 100, false))
    }

    private fun buildNotification(text: String, progress: Int, ongoing: Boolean): Notification {
        val contentIntent = PendingIntent.getActivity(
            this,
            0,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val builder = NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle(getString(R.string.notification_title))
            .setContentText(text)
            .setSmallIcon(android.R.drawable.stat_sys_download)
            .setContentIntent(contentIntent)
            .setOnlyAlertOnce(true)
            .setOngoing(ongoing)
            .setAutoCancel(!ongoing)

        if (progress < 0) {
            builder.setProgress(0, 0, true)
        } else if (ongoing) {
            builder.setProgress(100, progress, false)
        }
        return builder.build()
    }

    private fun createChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                CHANNEL_ID,
                getString(R.string.notification_channel),
                NotificationManager.IMPORTANCE_LOW,
            )
            val nm = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
            nm.createNotificationChannel(channel)
        }
    }

    override fun onDestroy() {
        scope.cancel()
        super.onDestroy()
    }
}
