package com.amuejunankar.ytdl

import android.content.ContentValues
import android.content.Context
import android.media.MediaScannerConnection
import android.net.Uri
import android.os.Build
import android.os.Environment
import android.provider.MediaStore
import java.io.File
import java.io.IOException

object MediaStoreSaver {

    fun save(context: Context, src: File, audio: Boolean): String {
        val mime = guessMime(src.extension, audio)
        val folder = if (audio) Environment.DIRECTORY_MUSIC else Environment.DIRECTORY_MOVIES
        val relative = "$folder/YouTube Downloader"

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            return saveToMediaStore(context, src, mime, relative)
        }

        @Suppress("DEPRECATION")
        val base = Environment.getExternalStoragePublicDirectory(folder)
        val dir = File(base, "YouTube Downloader")
        if (!dir.exists() && !dir.mkdirs()) {
            throw IOException("Cannot create folder $relative")
        }
        val dst = uniqueFile(dir, src.name)
        src.copyTo(dst, overwrite = false)
        MediaScannerConnection.scanFile(context, arrayOf(dst.absolutePath), arrayOf(mime), null)
        return "$relative/${dst.name}"
    }

    private fun saveToMediaStore(context: Context, src: File, mime: String, relative: String): String {
        val resolver = context.contentResolver
        val collection = when {
            mime.startsWith("audio/") -> MediaStore.Audio.Media.EXTERNAL_CONTENT_URI
            else -> MediaStore.Video.Media.EXTERNAL_CONTENT_URI
        }
        val relativePath = "$relative/"
        val displayName = uniqueDisplayName(resolver, collection, relativePath, src.name)

        val values = ContentValues().apply {
            put(MediaStore.MediaColumns.DISPLAY_NAME, displayName)
            put(MediaStore.MediaColumns.MIME_TYPE, mime)
            put(MediaStore.MediaColumns.RELATIVE_PATH, relativePath)
            put(MediaStore.MediaColumns.IS_PENDING, 1)
        }

        val uri: Uri = resolver.insert(collection, values)
            ?: throw IOException("MediaStore refused $relative/$displayName")

        try {
            resolver.openOutputStream(uri)?.use { out ->
                src.inputStream().use { it.copyTo(out) }
            } ?: throw IOException("Cannot open output for $displayName")

            val done = ContentValues().apply { put(MediaStore.MediaColumns.IS_PENDING, 0) }
            resolver.update(uri, done, null, null)
        } catch (t: Throwable) {
            runCatching { resolver.delete(uri, null, null) }
            throw t
        }

        return "$relative/$displayName"
    }

    private fun uniqueDisplayName(
        resolver: android.content.ContentResolver,
        collection: Uri,
        relativePath: String,
        name: String,
    ): String {
        if (!exists(resolver, collection, relativePath, name)) return name
        val dot = name.lastIndexOf('.')
        val stem = if (dot > 0) name.substring(0, dot) else name
        val ext = if (dot > 0) name.substring(dot) else ""
        var index = 1
        while (true) {
            val candidate = "$stem ($index)$ext"
            if (!exists(resolver, collection, relativePath, candidate)) return candidate
            index++
        }
    }

    private fun exists(
        resolver: android.content.ContentResolver,
        collection: Uri,
        relativePath: String,
        name: String,
    ): Boolean {
        val selection = "${MediaStore.MediaColumns.RELATIVE_PATH}=? AND ${MediaStore.MediaColumns.DISPLAY_NAME}=?"
        resolver.query(collection, arrayOf(MediaStore.MediaColumns._ID), selection, arrayOf(relativePath, name), null)
            ?.use { return it.count > 0 }
        return false
    }

    private fun uniqueFile(dir: File, name: String): File {
        var candidate = File(dir, name)
        if (!candidate.exists()) return candidate
        val dot = name.lastIndexOf('.')
        val stem = if (dot > 0) name.substring(0, dot) else name
        val ext = if (dot > 0) name.substring(dot) else ""
        var index = 1
        while (candidate.exists()) {
            candidate = File(dir, "$stem ($index)$ext")
            index++
        }
        return candidate
    }

    private fun guessMime(ext: String, audio: Boolean): String {
        return when (ext.lowercase()) {
            "mp4", "m4v" -> "video/mp4"
            "webm" -> "video/webm"
            "mkv" -> "video/x-matroska"
            "mov" -> "video/quicktime"
            "avi" -> "video/x-msvideo"
            "mp3" -> "audio/mpeg"
            "m4a" -> "audio/mp4"
            "opus" -> "audio/opus"
            "ogg" -> "audio/ogg"
            "aac" -> "audio/aac"
            "flac" -> "audio/flac"
            "wav" -> "audio/wav"
            else -> if (audio) "audio/mpeg" else "video/mp4"
        }
    }
}
