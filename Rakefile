require 'socket'
require 'open3'
require 'json'
require 'fileutils'
require 'tmpdir'
require 'date'

ROOT = File.expand_path(__dir__)
SOURCE = ENV.fetch('SOURCE', 'data/source.json')
SPEC = JSON.parse(File.read(SOURCE))
DUMP = ENV.fetch('DUMP', File.join('data/dumps', SPEC.fetch('filename')))
DB = ENV.fetch('DB', 'data/dic.sqlite')
OUTPUT = ENV.fetch('OUTPUT', 'dist')
VECTORS = ENV.fetch('VECTORS', 'data/vectors.sqlite')
PYTHON_SOURCES = FileList['src/dic/*.py']
# Display/image version (package.json keeps the literal 0.0.1-SNAPSHOT form;
# pyproject.toml uses the PEP 440 spelling 0.0.1+SNAPSHOT).
IMAGE_TAG = JSON.parse(File.read('package.json')).fetch('version')
REGISTRY = 'mini.local:5000'
IMAGE = "#{REGISTRY}/editii/conventum:#{IMAGE_TAG}"

def load_profile!(profile)
  store = ENV.fetch('PASSWORD_STORE_DIR', File.expand_path('~/.password-store'))
  selected = profile || ENV['PROFILE']
  machine = Socket.gethostname.split('.').first
  selected ||= File.directory?(File.join(store, 'dic', machine)) ? machine : 'local'
  return if selected == 'local' || @loaded_profile == selected
  loader = File.join(store, 'load-env.sh')
  abort "No pass-store loader at #{loader}" unless File.file?(loader)
  output, status = Open3.capture2('bash', '-euc', 'source "$1" "$2"; env -0', 'dic', loader, "dic/#{selected}")
  abort "Could not load dic/#{selected}" unless status.success?
  output.split("\0").each do |entry|
    key, value = entry.split('=', 2)
    ENV[key] = value if key && value && !key.start_with?('_')
  end
  @loaded_profile = selected
end

def cli(*arguments)
  sh 'uv', 'run', '--frozen', 'dic', *arguments
end

# The pinned manifest, not "latest", defines the reproducible source snapshot.
file DUMP => [SOURCE] do
  load_profile!(nil)
  cli 'download', '--source', SOURCE, '--directory', File.dirname(DUMP)
  expected = File.join(File.dirname(DUMP), SPEC.fetch('filename'))
  abort "DUMP must use the filename recorded in #{SOURCE}" unless expected == DUMP
end

file DB => [DUMP, SOURCE, *PYTHON_SOURCES, 'uv.lock'] do
  load_profile!(nil)
  epoch = DateTime.strptime(SPEC.fetch('dump_date'), '%Y%m%d').to_time.to_i.to_s
  sh({'SOURCE_DATE_EPOCH'=>epoch}, 'uv', 'run', '--frozen', 'dic', 'import', DUMP,
     '--database', DB, '--edition', SPEC.fetch('edition'))
end

desc 'Download the pinned Wiktionary XML snapshot and verify SHA-1 (cache reused)'
task :download => [DUMP] do
  cli 'download', '--source', SOURCE, '--directory', File.dirname(DUMP)
end

desc 'Download if needed and incrementally populate the dictionary SQLite database'
task :populate => [:download, DB]

desc 'Force reimport of the pinned snapshot; preserves the old DB until success'
task :import => [:download] do
  Rake::Task[DB].execute
end

desc 'Install locked Node dependencies used by the browser and test suite'
task :node_deps do
  if !File.file?('node_modules/.dic-lock') || File.read('node_modules/.dic-lock') != File.read('package-lock.json')
    sh 'npm', 'ci', '--ignore-scripts', '--no-audit'
    File.write('node_modules/.dic-lock', File.read('package-lock.json'))
  end
end

desc 'Install the Chromium version pinned by Playwright'
task :browser => [:node_deps] do
  sh 'npx', '--no-install', 'playwright', 'install', 'chromium'
end

desc 'Install locked Python and Node dependencies'
task :setup, [:profile] => [:node_deps] do |_, args|
  load_profile!(args[:profile])
  sh 'uv', 'sync', '--frozen'
end

desc 'Build the static site and SQLite export from the versioned database (no XML download)'
task :build, [:profile] do |_, args|
  load_profile!(args[:profile])
  abort "Missing #{DB}; run rake populate" unless File.file?(DB)
  cli 'build', '--database', DB, '--output', OUTPUT, '--vectors', VECTORS
end

desc 'Compute/resume BGE-M3 embeddings; cache stays local, scores are published into the dictionary'
task :embed, [:profile] do |_, args|
  load_profile!(args[:profile])
  options = ['embed', '--database', DB, '--vectors', VECTORS,
    '--endpoint', ENV.fetch('OLLAMA_URL','http://zmeu.local:11434'),
    '--model', ENV.fetch('MODEL','bge-m3:567m-fp16'),'--batch-size',ENV.fetch('BATCH_SIZE','64')]
  options += ['--dump',DUMP] if File.file?(DUMP) && ENV['DEFINITIONS'] != '0'
  cli(*options)
  Rake::Task[:build].invoke(args[:profile])
end

desc 'Populate from the pinned source, then build the static site'
task :refresh => [:populate, :build]

desc 'Serve Dic on the local network at port 8040; HOST=... PORT=...'
task :run, [:profile] do |_, args|
  load_profile!(args[:profile])
  Rake::Task[:build].invoke(args[:profile])
  cli 'serve', '--directory', OUTPUT, '--host', ENV.fetch('HOST','0.0.0.0'), '--port',ENV.fetch('PORT','8040')
end

desc 'Run every test: Python units, JavaScript units, and Playwright against a copied SQLite DB'
task :test, [:profile] => [:browser] do |_, args|
  load_profile!(args[:profile])
  sh 'uv','run','--frozen','python','-m','pytest','-q'
  Dir.mktmpdir('dic-test-') do |directory|
    test_db = File.join(directory,'dic.sqlite')
    FileUtils.cp(DB,test_db)
    cli 'build','--database',test_db,'--output',File.join(directory,'site')
    sh({'DIC_STATIC_DIR'=>File.join(directory,'site')},'node','tests/browser.test.cjs')
  end
end

desc 'Install dependencies, run every test, build distributions, build and publish the Docker image'
task :ci, [:profile] => [:setup] do |_, args|
  load_profile!(args[:profile])
  Rake::Task[:test].invoke(args[:profile])
  sh 'uv','build','--out-dir','dist/packages'
  sh 'docker','build','--tag',IMAGE,'.'
  sh 'docker','push',IMAGE
end

task default: :test
