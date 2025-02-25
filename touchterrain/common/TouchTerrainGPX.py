"""Module to add GPX paths to a TouchTerrain 3D model
   (Aug. 4, 2020, by KohlhardC)
   This is accomplished by adjusting the height data that is used to generate the 3D model such that the GPX paths are shown with higher or lower elevations therby creating a visible path. 
"""
import time 
import math

def plotLineHigh(x0,y0,x1,y1,height,npim,pathedPoints,thicknessOffset): 
    """ Draw a line in the npim array using using Bresenham's line algorithm as shown here: https://en.wikipedia.org/wiki/Bresenham%27s_line_algorithm. This function is for lines where y increases. 

    Args:
        x0 (int): x position of the start of the primary line 
        y0 (int): y position of the start of the primary line 
        x1 (int): x position of the end of the primary line 
        y1 (int): y position of the end of the primary line
        height (int): height offset, in meters, from the terrain elevation to denote a GPX track. Negative numbers are ok. 
        npim (numpy array): The numpy array containing elevation data for points on the 3D terrain map 
        pathedPoints (dictionary): A dictionary the keeps track of points that have already been adjusted to mark a GPX path
        thicknessOffset (int): The number of pixels, positive or negative, to offset the drawn line from the primary line described by x0,y0 : x1,y1

    """ 
    dx = x1 - x0
    dy = y1 - y0
    xi = 1

    if dx < 0:
        xi = -1
        dx = -dx
    
    D = 2 * dx - dy
    x = x0

    for y in range(y0,y1+1):
        plotPoint(x,y,height,npim,pathedPoints,thicknessOffset)
        if D > 0:
            x = x + xi
            D = D - 2 * dy

        D = D + 2 * dx

def plotLineLow(x0,y0,x1,y1,height,npim,pathedPoints,thicknessOffset):
    """ Draw a line in the npim array using using Bresenham's line algorithm as shown here: https://en.wikipedia.org/wiki/Bresenham%27s_line_algorithm. This function is for lines where y decreases. 

    Args:
        x0 (int): x position of the start of the primary line 
        y0 (int): y position of the start of the primary line 
        x1 (int): x position of the end of the primary line 
        y1 (int): y position of the end of the primary line
        height (int): height offset, in meters, from the terrain elevation to denote a GPX track. Negative numbers are ok. 
        npim (numpy array): The numpy array containing elevation data for points on the 3D terrain map 
        pathedPoints (dictionary): A dictionary the keeps track of points that have already been adjusted to mark a GPX path
        thicknessOffset (int): The number of pixels, positive or negative, to offset the drawn line from the primary line described by x0,y0 : x1,y1
    """

    dx = x1 - x0
    dy = y1 - y0
    yi = 1

    if dy < 0: 
        yi = -1
        dy = -dy
    
    D = 2 * dy - dx
    y = y0

    for x in range(x0, x1+1): 
        plotPoint(x,y,height,npim,pathedPoints,thicknessOffset)
        if D > 0: 
            y = y + yi
            D = D - 2 * dx
        D = D + 2 * dy

def plotLine(x0,y0,x1,y1,height,npim,pathedPoints,thicknessOffset):
    """ Draw a line in the npim array using using Bresenham's line algorithm as shown here: https://en.wikipedia.org/wiki/Bresenham%27s_line_algorithm

    Args:
        x0 (int): x position of the start of the primary line 
        y0 (int): y position of the start of the primary line 
        x1 (int): x position of the end of the primary line 
        y1 (int): y position of the end of the primary line
        height (int): height offset, in meters, from the terrain elevation to denote a GPX track. Negative numbers are ok. 
        npim (numpy array): The numpy array containing elevation data for points on the 3D terrain map 
        pathedPoints (dictionary): A dictionary the keeps track of points that have already been adjusted to mark a GPX path
        thicknessOffset (int): The number of pixels, positive or negative, to offset the drawn line from the 
                               primary line described by x0,y0 : x1,y1
    """
    #pr(" plotLine") 
    if abs(y1 - y0) < abs(x1 - x0):
        if x0 > x1: 
             plotLineLow(x1,y1,x0,y0,height,npim,pathedPoints,thicknessOffset ) 
        else: 
             plotLineLow(x0,y0,x1,y1,height,npim,pathedPoints,thicknessOffset ) 
    else:
        if y0 > y1: 
            plotLineHigh(x1,y1,x0,y0,height,npim,pathedPoints,thicknessOffset )  
        else: 
            plotLineHigh(x0,y0,x1,y1,height,npim,pathedPoints,thicknessOffset )           
 
def plotPoint(x,y,height,npim,pathedPoints,thicknessOffset):
    """ Draw a line in the npim array using using Bresenham's line algorithm as shown here: https://en.wikipedia.org/wiki/Bresenham%27s_line_algorithm

    Args:
        x (int): x position of the primary point
        y (int): y position of the primary point
        height (int): height offset, in meters, from the terrain elevation to denote a GPX track. Negative numbers are ok. 
        npim (numpy array): The numpy array containing elevation data for points on the 3D terrain map 
        pathedPoints (dictionary): A dictionary the keeps track of points that have already been adjusted to mark a GPX path
        thicknessOffset (int): The number of pixels, positive or negative, to offset the drawn point from
                               the primary point described by x,y 

    """ 
    plotY = y + thicknessOffset 
    plotX = x + thicknessOffset 
    pointKey = str(plotX) + "x" + str(plotY)  
    
    #print("  plotting: {0}".format(pointKey) )

    # Only update a point if we haven't already done something to it
    if pointKey not in pathedPoints: 
        if thicknessOffset == 0:
            newHeight = height + npim[x][y]  
        else:
            # get the height from the primary line so that thicker lines appear flat
            newHeight = npim[x][y] 
        
        try:
            npim[plotX][plotY] = newHeight # might be outside of npim
        except:
            print(f"    npim coords {plotX} {plotY} are out of bounds!")
        
       
        pathedPoints[pointKey] = True 
    else: 
        pass
        #print("   skipped:") 

def convert_to_GeoJSON(importedGPX):
    ''' reads in gpx files from list of filenames and returns a ee.Geometry.MultiLineString'''
    import xml.etree.ElementTree as ET 
    import ee # I'm assuming that ee.Initialize() was already done!
    line_list = []

    for gpxFile in importedGPX:
        tree = ET.parse(gpxFile)
        root = tree.getroot() 
        points = root.find('{http://www.topografix.com/GPX/1/1}trk/{http://www.topografix.com/GPX/1/1}trkseg') 
        line = []

        for trkpt in points:
            gpx_lat = float( trkpt.attrib['lat'] )
            gpx_lon = float( trkpt.attrib['lon'] ) 
            line.append([gpx_lon, gpx_lat])
        line_list.append(line)

    mls = ee.Geometry.MultiLineString(line_list)
    return mls

def addGPXToModel(pr,npim,dem,importedGPX,gpxPathHeight,gpxPixelsBetweenPoints,gpxPathThickness,trlat,trlon,bllat,bllon):
    """ Add 1 or more GPX tracks to the terrain model

    Args:
        pr (function): reference to the logging function
        npim (2d numpy array): The numpy array containing elevation data for points on the 3D terrain map
        dem (GDAL raster dataset): The GDAL raster dataset is needed since it contains the projection used by
                                   the elevation data
        importedGPX (list): List of strings which reference the file paths for GPX tracks.
        gpxPathHeightHeight (int): height offset, in meters, from the terrain elevation to denote a GPX track.
                                   Negative numbers are ok.
        gpxPixelsBetweenPoints (int): GPX Files can have a lot of points. This argument controls how
                                      many pixel distance there should be between points, effectively causing fewing
                                      lines to be drawn. A higher number will create more space between lines drawn
                                      on the model and can have the effect of making the paths look a bit cleaner at
                                      the expense of less precision
        gpxPathThickness (int): Stacks parallel lines on either side of the primary line to create thickness.
        trlat (float): top right latitude of the terrain map.
        trlon (float): top right longitude of the terrain map.
        bllat (float): bottom left latitude of the terrain map.
        bllat (float): bottom left longitude of the terrain map.

    Returns:
        a modified npim array that now contains adjusted elevation data such that the GPX path(s) will be
        recognizable on the terrain model

    """
    import xml.etree.ElementTree as ET

    # check if we have to explicitly override the PROJ_LIB folder so osr has access to
    # the projection database proj.db. Otherwise  source.ImportFromEPSG(4326) (setting up WGS84)
    # won't work and you'll get this:
    # ERROR 1: PROJ: proj_create_from_database: Cannot find proj.db
    # ERROR 1: PROJ: proj_create: unrecognized format / unknown name
    # ERROR 6: Cannot find coordinate operations from `' to `PROJCRS["WGS 84 / .... (your DEM SRS which did work b/c it used a different call ...)
    # however this is just printed out somewhere (stderr?) and doesn't generate an exception! (Super helpful ...)

    # Confusingly, what really bombs is transform.TransformPoint(gpx_lat, gpx_lon) later, when
    # it can't transform into the non-existing target SRS and, even more "helpfully" then complains
    # about a missing signature for the underlying C++ method:
    #   NotImplementedError: Wrong number or type of arguments for overloaded function 'CoordinateTransformation_TransformPoint'.
    #   Wrong number or type of arguments for overloaded function 'CoordinateTransformation_TransformPoint'.
    #       Possible C/C++ prototypes are:
    #       OSRCoordinateTransformationShadow::TransformPoint(double [3])
    #       OSRCoordinateTransformationShadow::TransformPoint(double [4])
    #       OSRCoordinateTransformationShadow::TransformPoint(double [3],double,double,double)
    #       OSRCoordinateTransformationShadow::TransformPoint(double [4],double,double,double,double)
    # In my case (Win10) the proj folder had been installed but the PROJ_LIB env var didn't point to it for some reason.
    # If this happens to you, figure out which folder proj.db is in (for me it's anaconda3\Lib\site-packages\osgeo\data\proj) and
    # put it into common/config.py under PROJ_DIR

    from touchterrain.common import config # non-server config settings
    if config.PROJ_DIR != None: # we got an override setting!
        import os
        os.environ['PROJ_LIB'] = config.PROJ_DIR
        print("PROJ_LIB OSGEO projection folder was set to", os.environ['PROJ_LIB'])

    # Later versions of osr may be bundled into osgeo so check there as well.
    try:
        import osr
    except ImportError as err:
        from osgeo import osr
    import time
    import math

    gpxStartTime = time.time()
    pathedPoints = {}

    # Parse GPX file(s)
    ulx, xres, xskew, uly, yskew, yres  = dem.GetGeoTransform()
    target = osr.SpatialReference()
    t_res = target.ImportFromWkt(dem.GetProjection()) # return of 0 => OK
    if t_res != 0: assert False, "addGPXToMode(): target.ImportFromWkt() returned error" + str(t_res)
    source = osr.SpatialReference()
    s_res = source.ImportFromEPSG(4326) # This is WGS84, return of 0 => OK
    if s_res != 0: assert False, "addGPXToMode(): source.ImportFromEPSG(4326) returned error" + str(s_res)

    for gpxFile in importedGPX:
        pr(f"process gpx file: {gpxFile}")

        # parse file for points and tracks
        tree = ET.parse(gpxFile)
        root = tree.getroot()
        points = root.find('{http://www.topografix.com/GPX/1/1}trk/{http://www.topografix.com/GPX/1/1}trkseg')
        tracks = root.findall('{http://www.topografix.com/GPX/1/1}trk/{http://www.topografix.com/GPX/1/1}trkseg')

        numTracks = len(tracks)
        pr(numTracks, "GPX tracks found")

        for trk in tracks:
            pr("Plotting track", numTracks)
            numTracks -= 1

            # We need to keep track of the last point so that we can draw a line between points
            lastPoint = None
            count = 0

            for trkpt in trk:
                count = count + 1
                gpx_lat = float(trkpt.attrib['lat'])
                gpx_lon = float(trkpt.attrib['lon'])
                #pr("  Process GPX Point: Lat: {0} Lon: {1}:".format( gpx_lat, gpx_lon ) )

                transform = osr.CoordinateTransformation(source,target )
                projectedPoints = transform.TransformPoint(gpx_lat, gpx_lon)

                rasterX = int( (projectedPoints[1] - uly) / yres )
                rasterY = int( (projectedPoints[0] - ulx) / xres )

                # Only process this point if it's in the bounds
                if rasterX >= 0 and rasterX < npim.shape[0] and rasterY >=0 and rasterY < npim.shape[1]:

                    currentPoint = (rasterX,rasterY)

                    # Draw line between two points using Bresenham's line algorithm
                    if lastPoint is not None:
                        # Calculate distance between last point and current point
                        # Only plot the point if it's far away. Helps cull some GPX points
                        dist = math.sqrt((rasterX - lastPoint[0])**2 + (rasterY - lastPoint[1])**2)

                        # Only render the GPX point if it's beyond the specified distance OR
                        # if it's the last point
                        if dist >= gpxPixelsBetweenPoints or count == len(points) -1:
                            #try creating a dashed path by plotting every other line

                            #pr("primaryLine")
                            plotLine(lastPoint[0],lastPoint[1],currentPoint[0],currentPoint[1],gpxPathHeight,npim,pathedPoints,0)

                            # Create line thickness by stacking lines
                            thicknessOffset = 1

                            for loopy in range(1, int(gpxPathThickness) ):

                                #pr("thickerLine")
                                plotLine(lastPoint[0],lastPoint[1],currentPoint[0],currentPoint[1],gpxPathHeight,npim,pathedPoints,thicknessOffset)

                                # Alternate sides of line to draw on when adding thickness
                                if (loopy % 2) == 0:
                                    thicknessOffset = (thicknessOffset * -1) + 1
                                else:
                                    thicknessOffset = thicknessOffset * -1
                            lastPoint = currentPoint
                    else:
                        lastPoint = currentPoint
                else:
                    # If a point is out of bounds, we need to invalidate lastPoint
                    #pr("out of bounds: {0},{1}".format(gpx_lat, gpx_lon) )
                    lastPoint = None

    gpxEndTime = time.time()
    gpxElapsedTime = gpxEndTime - gpxStartTime
    pr(f"Time to add GPX paths:{gpxElapsedTime}")
