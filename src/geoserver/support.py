from xml.etree.ElementTree import TreeBuilder, XML, tostring
from tempfile import mkstemp
from urllib2 import urlopen, HTTPPasswordMgr, HTTPBasicAuthHandler, install_opener, build_opener
from zipfile import ZipFile

class ResourceInfo(object):
  resource_type = 'abstractResourceType'

  def update(self):
    self.metadata = get_xml(self.href)
    self.name = self.metadata.find('name').text

  def serialize(self):
    builder = TreeBuilder()
    builder.start(self.resource_type, dict())
    self.encode(builder)
    builder.end(self.resource_type)
    return tostring(builder.close())

  def encode(self, builder):
    """
    Add appropriate XML nodes to this object.  The builder will be passed in
    ready to go, with the appropriate top-level node already added.
    """
    pass

def prepare_upload_bundle(name, data):
  """GeoServer's REST API uses ZIP archives as containers for file formats such
  as Shapefile and WorldImage which include several 'boxcar' files alongside
  the main data.  In such archives, GeoServer assumes that all of the relevant
  files will have the same base name and appropriate extensions, and live in
  the root of the ZIP archive.  This method produces a zip file that matches
  these expectations, based on a basename, and a dict of extensions to paths or
  file-like objects. The client code is responsible for deleting the zip
  archive when it's done."""

  handle, f = mkstemp() # we don't use the file handle directly. should we?
  zip = ZipFile(f, 'w')
  for ext, stream in data.iteritems():
    fname = "%s.%s" % (name, ext)
    if (isinstance(stream, basestring)):
      zip.write(stream, fname)
    else:
      zip.writestr(fname, stream.read())
  zip.close()
  return f

def get_xml(url):
  password_manager = HTTPPasswordMgr()
  password_manager.add_password(
    realm='GeoServer Realm',
    uri='http://localhost:8080/geoserver/',
    user='admin',
    passwd='geoserver'
  )

  handler = HTTPBasicAuthHandler(password_manager)
  install_opener(build_opener(handler))
  
  response = urlopen(url).read()
  try:
    return XML(response)
  except:
    print "%s => \n%s" % (url, response)

def atom_link(node):
  return node.find("{http://www.w3.org/2005/Atom}link").get("href")
