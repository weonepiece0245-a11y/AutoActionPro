package com.example.ui.theme

import android.os.Build
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.dynamicDarkColorScheme
import androidx.compose.material3.dynamicLightColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.platform.LocalContext

import androidx.compose.ui.graphics.Color

private val DarkColorScheme =
  darkColorScheme(
    primary = BoldIceBlue,
    onPrimary = BoldNavy,
    secondary = BoldSteel,
    onSecondary = Color.White,
    tertiary = BoldSalmon,
    onTertiary = BoldNavy,
    background = BoldNavy,
    surface = Color(0xFF0C244C), // Deep dark navy surface
    onBackground = Color.White,
    onSurface = Color.White,
    outline = BoldSteel
  )

private val LightColorScheme =
  lightColorScheme(
    primary = BoldNavy,
    onPrimary = Color.White,
    secondary = BoldSteel,
    onSecondary = Color.White,
    tertiary = BoldSalmon,
    onTertiary = BoldNavy,
    background = BoldBackground,
    surface = Color.White,
    onBackground = BoldNavy,
    onSurface = BoldNavy,
    outline = BoldIceBlue
  )

@Composable
fun MyApplicationTheme(
  darkTheme: Boolean = false, // Default to false to show the beautiful Bold Light Theme
  // Dynamic color is available on Android 12+
  dynamicColor: Boolean = false,
  content: @Composable () -> Unit,
) {
  val colorScheme = if (darkTheme) DarkColorScheme else LightColorScheme

  MaterialTheme(colorScheme = colorScheme, typography = Typography, content = content)
}
