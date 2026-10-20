package com.amuejunankar.ytdl

import android.Manifest
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.res.Configuration
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.os.Build
import android.os.Bundle
import android.view.View
import android.widget.ArrayAdapter
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import com.amuejunankar.ytdl.databinding.ActivityMainBinding
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.net.HttpURLConnection
import java.net.URL
import java.util.Locale

class MainActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainBinding

    private val qualities: List<Pair<String, String>> = listOf(
        "Best available" to "bv*+ba/b",
        "2160p" to "bv*[height<=2160]+ba/b[height<=2160]/b",
        "1440p" to "bv*[height<=1440]+ba/b[height<=1440]/b",
        "1080p" to "bv*[height<=1080]+ba/b[height<=1080]/b",
        "720p" to "bv*[height<=720]+ba/b[height<=720]/b",
        "480p" to "bv*[height<=480]+ba/b[height<=480]/b",
        "360p" to "bv*[height<=360]+ba/b[height<=360]/b",
    )

    private var engineReady = false
    private var busy = false
    private var analyzing = false
    private var pendingAutoAnalyze = false
    private var pendingUrl: String? = null

    private val notificationPermission =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { }

    private val storagePermission =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
            if (granted) startDownload() else toast(getString(R.string.storage_needed))
        }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        applyEdgeToEdge()
        setupQualityDropdown()
        setupActions()
        observeEngine()
        observeDownloads()
        requestNotificationPermissionIfNeeded()
        handleIntent(intent)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        handleIntent(intent)
    }

    private fun applyEdgeToEdge() {
        WindowCompat.setDecorFitsSystemWindows(window, false)
        ViewCompat.setOnApplyWindowInsetsListener(binding.root) { view, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            view.setPadding(bars.left, bars.top, bars.right, bars.bottom)
            insets
        }
        val night = resources.configuration.uiMode and
            Configuration.UI_MODE_NIGHT_MASK == Configuration.UI_MODE_NIGHT_YES
        WindowInsetsControllerCompat(window, binding.root).isAppearanceLightStatusBars = !night
    }

    private fun setupQualityDropdown() {
        val labels = qualities.map { it.first }
        val adapter = ArrayAdapter(this, android.R.layout.simple_list_item_1, labels)
        binding.qualityInput.setAdapter(adapter)
        binding.qualityInput.setText(labels.first(), false)
    }

    private fun setupActions() {
        binding.urlLayout.setEndIconOnClickListener { pasteFromClipboard() }
        binding.analyzeButton.setOnClickListener { analyze() }
        binding.downloadButton.setOnClickListener { startDownload() }
        binding.cancelButton.setOnClickListener { DownloadService.cancel(this) }
    }

    private fun pasteFromClipboard() {
        val clipboard = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
        val text = clipboard.primaryClip?.getItemAt(0)?.coerceToText(this)?.toString()
        if (text.isNullOrBlank()) {
            toast(getString(R.string.paste_empty))
        } else {
            binding.urlInput.setText(text.trim())
            analyze()
        }
    }

    private fun analyze() {
        if (analyzing) return
        if (!engineReady) {
            toast(getString(R.string.engine_starting))
            return
        }
        val url = binding.urlInput.text?.toString()?.trim().orEmpty()
        if (url.isEmpty()) {
            toast(getString(R.string.paste_empty))
            return
        }

        analyzing = true
        binding.analyzeButton.isEnabled = false
        binding.statusText.text = getString(R.string.analyzing)

        lifecycleScope.launch {
            val result = withContext(Dispatchers.IO) { runCatching { Engine.fetchInfo(url) } }
            analyzing = false
            binding.analyzeButton.isEnabled = engineReady

            result.onSuccess { info ->
                binding.videoCard.visibility = View.VISIBLE
                binding.titleText.text = info.title ?: info.fulltitle ?: ""
                val duration = formatDuration(info.duration)
                val uploader = info.uploader ?: ""
                binding.metaText.text = listOf(uploader, duration)
                    .filter { it.isNotBlank() }
                    .joinToString("  ·  ")
                binding.statusText.text = getString(R.string.video_ready)
                loadThumbnail(info.thumbnail)
            }.onFailure { error ->
                binding.videoCard.visibility = View.GONE
                binding.statusText.text = getString(R.string.analysis_failed) + ": " + (error.message ?: "")
            }
        }
    }

    private fun startDownload() {
        if (busy) return
        if (!engineReady) {
            toast(getString(R.string.engine_starting))
            return
        }
        val url = binding.urlInput.text?.toString()?.trim().orEmpty()
        if (url.isEmpty()) {
            toast(getString(R.string.paste_empty))
            return
        }
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) {
            val granted = ContextCompat.checkSelfPermission(
                this,
                Manifest.permission.WRITE_EXTERNAL_STORAGE,
            ) == PackageManager.PERMISSION_GRANTED
            if (!granted) {
                storagePermission.launch(Manifest.permission.WRITE_EXTERNAL_STORAGE)
                return
            }
        }

        val label = binding.qualityInput.text?.toString().orEmpty()
        val format = qualities.firstOrNull { it.first == label }?.second ?: "bv*+ba/b"
        DownloadService.start(
            this,
            url,
            format,
            binding.audioSwitch.isChecked,
            binding.playlistSwitch.isChecked,
        )
    }

    private fun handleIntent(intent: Intent?) {
        val url = extractUrl(intent) ?: return
        binding.urlInput.setText(url)
        pendingUrl = url
        pendingAutoAnalyze = true
        maybeAutoAnalyze()
    }

    private fun extractUrl(intent: Intent?): String? {
        if (intent == null) return null
        val raw = when (intent.action) {
            Intent.ACTION_VIEW -> intent.data?.toString()
            Intent.ACTION_SEND -> intent.getStringExtra(Intent.EXTRA_TEXT)
            else -> null
        } ?: return null
        return Regex("https?://\\S+").find(raw)?.value?.trimEnd('.', ',', ')', '"', '\'')
    }

    private fun maybeAutoAnalyze() {
        if (!pendingAutoAnalyze || !engineReady || pendingUrl == null) return
        val url = pendingUrl ?: return
        if (binding.urlInput.text?.toString() != url) return
        pendingAutoAnalyze = false
        pendingUrl = null
        analyze()
    }

    private fun loadThumbnail(url: String?) {
        if (url.isNullOrBlank()) {
            binding.thumbnail.setImageDrawable(null)
            return
        }
        lifecycleScope.launch {
            val bitmap = withContext(Dispatchers.IO) { downloadBitmap(url) }
            if (bitmap != null) binding.thumbnail.setImageBitmap(bitmap)
        }
    }

    private fun downloadBitmap(url: String): Bitmap? = try {
        val connection = (URL(url).openConnection() as HttpURLConnection).apply {
            connectTimeout = 15_000
            readTimeout = 15_000
            instanceFollowRedirects = true
        }
        connection.inputStream.use { BitmapFactory.decodeStream(it) }
    } catch (t: Throwable) {
        null
    }

    private fun observeEngine() {
        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                Engine.state.collect { state ->
                    engineReady = state.ready
                    binding.engineStatus.text = state.message
                    binding.analyzeButton.isEnabled = state.ready && !analyzing
                    binding.downloadButton.isEnabled = state.ready && !busy
                    if (state.ready) maybeAutoAnalyze()
                }
            }
        }
    }

    private fun observeDownloads() {
        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                DownloadBus.status.collect { status ->
                    busy = status.running && !status.finished && status.error == null
                    binding.cancelButton.isEnabled = busy
                    binding.downloadButton.isEnabled = !busy && engineReady

                    when {
                        status.error != null -> {
                            binding.progress.visibility = View.INVISIBLE
                            binding.statusText.text = getString(R.string.failed) + ": " + status.error
                        }
                        status.finished -> {
                            binding.progress.visibility = View.VISIBLE
                            binding.progress.isIndeterminate = false
                            binding.progress.setProgress(100)
                            binding.statusText.text = status.message
                        }
                        status.running -> {
                            binding.progress.visibility = View.VISIBLE
                            if (status.progress >= 0f) {
                                binding.progress.isIndeterminate = false
                                binding.progress.setProgress(status.progress.toInt().coerceIn(0, 100))
                            } else {
                                binding.progress.isIndeterminate = true
                            }
                            val eta = if (status.etaSeconds > 0) {
                                "  ETA " + formatDuration(status.etaSeconds.toInt())
                            } else {
                                ""
                            }
                            val pct = if (status.progress >= 0f) "  ${status.progress.toInt()}%" else ""
                            binding.statusText.text = (status.message + pct + eta).trim()
                        }
                        else -> {
                            binding.progress.visibility = View.INVISIBLE
                            if (status.message.isNotBlank()) binding.statusText.text = status.message
                        }
                    }
                }
            }
        }
    }

    private fun requestNotificationPermissionIfNeeded() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            val granted = ContextCompat.checkSelfPermission(
                this,
                Manifest.permission.POST_NOTIFICATIONS,
            ) == PackageManager.PERMISSION_GRANTED
            if (!granted) notificationPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
    }

    private fun formatDuration(totalSeconds: Int): String {
        if (totalSeconds <= 0) return ""
        val hours = totalSeconds / 3600
        val minutes = (totalSeconds % 3600) / 60
        val seconds = totalSeconds % 60
        return if (hours > 0) {
            String.format(Locale.US, "%d:%02d:%02d", hours, minutes, seconds)
        } else {
            String.format(Locale.US, "%d:%02d", minutes, seconds)
        }
    }

    private fun toast(message: String) {
        Toast.makeText(this, message, Toast.LENGTH_SHORT).show()
    }
}
