from flask import Response, Flask, render_template
import threading
import datetime
import time
import cv2
import logging
import os

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize Flask application
app = Flask(__name__)

# Global variables for thread-safe frame handling
outputFrame = None
lock = threading.Lock()

# RTSP stream configuration
RTSP_URL = "rtsp://admin:Kominfo456@103.143.154.5:557/h264"
FRAME_WIDTH = 1280  # Increased resolution
FRAME_HEIGHT = 720  # Increased resolution

def initialize_camera():
    """Initialize and configure the camera with RTSP stream"""
    try:
        # Try GStreamer pipeline first
        gst_str = (f'rtspsrc location={RTSP_URL} latency=0 ! '
                   'rtph264depay ! h264parse ! avdec_h264 ! '
                   'videoconvert ! appsink')
        camera = cv2.VideoCapture(gst_str, cv2.CAP_GSTREAMER)
        
        if not camera.isOpened():
            # Fallback to FFMPEG if GStreamer fails
            logger.info("Falling back to FFMPEG backend")
            camera = cv2.VideoCapture(RTSP_URL, cv2.CAP_FFMPEG)
    except:
        # If GStreamer fails completely, use FFMPEG
        camera = cv2.VideoCapture(RTSP_URL, cv2.CAP_FFMPEG)
    
    if not camera.isOpened():
        raise RuntimeError("Failed to open RTSP stream")
        
    # Configure stream parameters
    camera.set(cv2.CAP_PROP_BUFFERSIZE, 4)  # Increased buffer size
    camera.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'H264'))
    camera.set(cv2.CAP_PROP_FPS, 30)  # Increased FPS
    
    # Additional optimizations
    camera.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_WIDTH)
    camera.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)
    
    return camera

def stream_camera():
    """Function to capture and process camera stream"""
    global outputFrame, lock
    
    # Initialize camera
    camera = initialize_camera()
    consecutive_failures = 0
    last_frame_time = time.time()
    frame_interval = 1.0 / 30  # Target 30 FPS
    
    try:
        while True:
            try:
                # Control frame rate
                current_time = time.time()
                if current_time - last_frame_time < frame_interval:
                    time.sleep(0.001)  # Small sleep to prevent CPU overload
                    continue
                
                success, frame = camera.read()
                
                if not success:
                    consecutive_failures += 1
                    logger.warning(f"Failed to read frame (attempt {consecutive_failures})")
                    
                    if consecutive_failures > 5:
                        logger.error("Too many consecutive failures, reconnecting...")
                        camera.release()
                        time.sleep(2)
                        camera = initialize_camera()
                        consecutive_failures = 0
                    continue
                
                consecutive_failures = 0
                last_frame_time = current_time
                
                # Process frame
                if frame is not None and frame.shape[0] > 0:
                    # Drop frame if we're falling behind
                    if lock.locked():
                        continue
                        
                    frame = cv2.resize(frame, (FRAME_WIDTH, FRAME_HEIGHT))
                    
                    # Update the frame securely
                    with lock:
                        outputFrame = frame.copy()
                else:
                    logger.warning("Received empty frame")
                    
            except Exception as e:
                logger.error(f"Error in stream loop: {str(e)}")
                time.sleep(1)
                
    except Exception as e:
        logger.error(f"Stream thread error: {str(e)}")
    finally:
        camera.release()

def generate_frames():
    """Generate frames for the video feed"""
    global outputFrame, lock
    
    while True:
        with lock:
            if outputFrame is None:
                continue
                
            # Encode frame
            try:
                # Higher JPEG quality
                encode_params = [
                    int(cv2.IMWRITE_JPEG_QUALITY), 85,
                    int(cv2.IMWRITE_JPEG_OPTIMIZE), 1
                ]
                
                success, encoded_frame = cv2.imencode('.jpg', outputFrame, encode_params)
                
                if not success:
                    continue
                    
                # Yield the frame in byte format
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + 
                       encoded_frame.tobytes() + b'\r\n')
                       
            except Exception as e:
                logger.error(f"Error encoding frame: {str(e)}")
                continue

@app.route('/')
def index():
    """Video streaming home page"""
    return render_template('index.html')

@app.route('/embed')
def embed():
    """Embedded video streaming page"""
    return render_template('embed.html')
    
@app.after_request
def after_request(response):
    """Add headers to allow embedding"""
    response.headers.add('Access-Control-Allow-Origin', '*')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization')
    response.headers.add('Access-Control-Allow-Methods', 'GET,OPTIONS')
    return response

@app.route('/video_feed')
def video_feed():
    """Video streaming route"""
    return Response(
        generate_frames(),
        mimetype='multipart/x-mixed-replace; boundary=frame'
    )

@app.route('/health')
def health():
    """Health check endpoint"""
    return {"status": "healthy", "timestamp": time.time()}

if __name__ == '__main__':
    # Ensure templates directory exists
    if not os.path.exists('templates'):
        os.makedirs('templates')
    
    # Start the streaming thread
    stream_thread = threading.Thread(target=stream_camera)
    stream_thread.daemon = True
    stream_thread.start()
    
    # Start Flask application
    try:
        app.run(
            host='0.0.0.0',
            port=5001,
            debug=False,  # Set to False when using threads
            threaded=True,
            use_reloader=False
        )
    except KeyboardInterrupt:
        logger.info("Server shutting down...")
    except Exception as e:
        logger.error(f"Server error: {str(e)}")