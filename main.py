import argparse
import os
import sys

# --- yt-dlp Python library import ---
try:
    import yt_dlp
except ImportError:
    print("The 'yt-dlp' Python library is not installed.")
    print("Please install it in your Python environment (e.g., 'pip install yt-dlp').")
    yt_dlp = None  # So the script can still be parsed, but download will fail

# --- a-Shell JavaScript runtime ---
# YouTube needs an external JS runtime to solve its n-sig challenges. Importing this registers
# a-Shell's built-in `jsc` with yt-dlp; it is inert on every other platform.
if yt_dlp:
    import ashell_jsc


def get_documents_folder():
    """
    Returns the path to the user's Documents folder.
    Creates the folder if it doesn't exist.
    """
    # Get the home directory
    home_dir = os.path.expanduser('~')
    documents_folder = os.path.join(home_dir, 'Documents')

    # Create the Documents folder if it doesn't exist
    try:
        os.makedirs(documents_folder, exist_ok=True)
    except OSError as e:
        print(f'Error creating Documents folder at {documents_folder}: {e}')
        print('Please ensure you have permissions to create this directory, or create it manually.')
        return None
    return documents_folder


def download_video_with_library(url, output_dir, is_youtube=True):
    """
    Downloads video from the given URL using the yt-dlp Python library.
    Saves the video into the specified output_dir.
    Returns the full path to the downloaded video file, or None on failure.
    """
    if not yt_dlp:
        print('yt-dlp library not imported, cannot download video.')
        return None

    # Define the output template for the filename within the output_dir
    output_template = os.path.join(output_dir, '%(title)s.%(ext)s')

    if is_youtube:
        reason = ashell_jsc.unavailable_reason()
        if reason:
            print(f'Warning: cannot solve YouTube JS challenges because {reason}.')
            print('Some formats will be missing and quality may be lower than requested.')
            print('jsc diagnostic:')
            for line in ashell_jsc.diagnostic_report():
                print(line)
        else:
            print("JS challenge solver ready: a-Shell's jsc.")

        ydl_opts = {
            'format': 'bestvideo[height<=?1080][fps<=?60][vcodec!*=av0]+bestaudio/best',
            'outtmpl': output_template,
            'noplaylist': False,
            'quiet': False,
            'extract_flat': 'discard_in_playlist',
            'final_ext': 'mkv',
            # Lets yt-dlp fetch solver scripts matching its own version when the installed
            # yt-dlp-ejs package has drifted out of step. Works with any runtime; only
            # 'ejs:npm' is Deno/Bun-only.
            'remote_components': ['ejs:github'],
            # Client choice is constrained by what this platform can produce. Of the clients
            # needing neither authentication nor a PO token, yt-dlp defaults to android_vr,
            # whose media URLs YouTube is currently rejecting with HTTP 403. 'tv' needs no
            # auth and no PO token either, and does require the JS player -- which the jsc
            # solver provides. The web clients are deliberately not requested: their
            # GVS_PO_TOKEN_POLICY marks a PO token as required, and there is no token
            # provider here, so their formats would 403 as well.
            # android_vr stays as a fallback; tv has the higher client priority (40 vs 10),
            # so its formats win when both supply the same itag.
            'extractor_args': {'youtube': {'player_client': ['tv', 'android_vr']}},
            'fragment_retries': 10,
            'ignoreerrors': 'only_download',
            'merge_output_format': 'mkv',
            'postprocessors': [
                {'key': 'FFmpegVideoRemuxer', 'preferedformat': 'mkv'},
                {'key': 'FFmpegConcat', 'only_multi_video': True, 'when': 'playlist'},
            ],
            'retries': 10,
        }
    else:
        ydl_opts = {
            'outtmpl': output_template,
            'noplaylist': True,
            'quiet': False,
            'extract_flat': 'discard_in_playlist',
            'final_ext': 'mkv',
            'remote_components': ['ejs:github'],
            'fragment_retries': 10,
            'ignoreerrors': 'only_download',
            'merge_output_format': 'mkv',
            'postprocessors': [
                {'key': 'FFmpegVideoRemuxer', 'preferedformat': 'mkv'},
                {'key': 'FFmpegConcat', 'only_multi_video': True, 'when': 'playlist'},
            ],
            'retries': 10,
        }

    try:
        print(f'Starting download for URL: {url} with yt-dlp library.')
        print(f'Video will be saved to: {output_dir}')
        # print(f"Output options: {ydl_opts}") # Can be verbose

        downloaded_filepath = None

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # Get video info (optional, but good for getting the expected filename)
            try:
                info_dict = ydl.extract_info(url, download=False)
                expected_filepath_template = ydl.prepare_filename(info_dict)
                print(f'Anticipated file path: {expected_filepath_template}')
            except Exception as e_info:
                print(f'Could not pre-fetch video info (will proceed with download): {e_info}')
                # Fallback: construct a generic expected path if info extraction fails
                # This is less reliable as title and ext are unknown here.
                # The actual filename will be determined after download.
                expected_filepath_template = os.path.join(output_dir, 'downloaded_video')

            # Start the download
            error_code = ydl.download([url])

            if error_code == 0:
                # The download was successful.
                # The most reliable way to get the filename is to check what file was actually created,
                # especially if the title had special characters.
                # `prepare_filename` on the info_dict *after* download (if info_dict was updated)
                # or by listing the directory.

                # If `extract_info` was run with `download=True`, `info_dict.get('filepath')` might be populated.
                # Since we did download=False then ydl.download(), we find the file.

                # Let's try to use the `expected_filepath_template` if it exists.
                # yt-dlp might have slightly modified it (e.g. replacing invalid chars).
                if os.path.exists(expected_filepath_template):
                    downloaded_filepath = expected_filepath_template
                else:
                    # Fallback: Scan the directory if the exact expected path isn't found
                    # This is helpful if the title processing by yt-dlp changed the name slightly
                    # or if `expected_filepath_template` was a generic one.
                    files_in_output_dir = os.listdir(output_dir)
                    video_files = [
                        f
                        for f in files_in_output_dir
                        if f.lower().endswith(
                            ('.mp4', '.mkv', '.webm', '.mov', '.avi', '.flv')
                        )  # Common video extensions
                    ]

                    if not video_files:
                        print('Download reported success, ' + 'but no video files found in the output directory.')
                        return None

                    # If there are multiple video files, it's ambiguous.
                    # We can try to find one that matches the expected title somewhat,
                    # or just pick one.
                    # For simplicity, if `expected_filepath_template` didn't work,
                    # and there's only one video, use it.
                    if len(video_files) == 1:
                        downloaded_filepath = os.path.join(output_dir, video_files[0])
                        print(
                            f'Note: Using detected video file: {downloaded_filepath} '
                            + f'(expected path was {expected_filepath_template})'
                        )
                    elif video_files:  # Multiple video files
                        print(
                            f'Warning: Multiple video files found in {output_dir}.'
                            + f' This script will use the first one detected: {video_files[0]}'
                        )
                        print(
                            'Consider refining "outtmpl" or checking the directory if '
                            + 'this is not the desired file.'
                        )
                        downloaded_filepath = os.path.join(
                            output_dir, video_files[0]
                        )  # Or implement more sophisticated logic
                    else:  # No video files after all
                        print('Download reported success, ' + 'but could not identify the primary video file.')
                        return None

                if downloaded_filepath and os.path.exists(downloaded_filepath):
                    print(f'Download successful. Video saved to: {downloaded_filepath}')
                    return downloaded_filepath
                else:
                    # This case should ideally not be reached if error_code was 0
                    print('Error: Download reported success,' + 'but the final file path could not be confirmed.')
                    print(f'Checked expected path: {expected_filepath_template}')
                    return None
            else:
                print(f'yt-dlp download failed with error code: {error_code}')
                return None

    except yt_dlp.utils.DownloadError as e:
        print(f'A yt-dlp download error occurred: {e}')
        return None
    except Exception as e:
        print(f'An unexpected error occurred during library-based download: {e}')
        import traceback

        traceback.print_exc()
        return None


def main():
    parser = argparse.ArgumentParser(
        description='Download a video using the yt-dlp Python ' + 'library and save it to your Documents folder.'
    )
    parser.add_argument('url', help='The URL of the video to download.')

    args = parser.parse_args()
    video_url = args.url
    is_youtube = 'youtube.com' in video_url or 'youtu.be' in video_url

    if not yt_dlp:  # Check if library was successfully imported
        print('Exiting because yt-dlp library is not available.')
        sys.exit(1)

    # Get the user's Documents folder
    documents_folder = get_documents_folder()
    if not documents_folder:
        print('Could not determine or create Documents folder. Exiting.')
        sys.exit(1)

    print(f'Attempting to save video to: {documents_folder}')

    # Use the new function
    downloaded_file_path = download_video_with_library(video_url, documents_folder, is_youtube)

    if downloaded_file_path:
        print(f'\nProcess complete. Video should be available at: {downloaded_file_path}')
    else:
        print('\nFailed to download the video.')


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('Usage: python your_script_name.py <video_url>')
        print('Example: python your_script_name.py "https://www.youtube.com/watch?v=dQw4w9WgXcQ"')  # Example URL
        sys.exit(1)
    main()
