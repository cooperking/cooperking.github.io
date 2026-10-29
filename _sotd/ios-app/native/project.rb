# The app's own native parts, put into Capacitor's Xcode project (run by customise.sh, from _sotd/ios-app, after
# `npx cap add ios` has made ios/ and the files have been copied into it):
#  - into the App target: SOTDViewController.swift (the plugins), SotdCastPlugin.swift (Chromecast), SotdSiri.swift (Siri,
#    and the widgets' sign-in), SotdContacts.swift (Apple's contact picker, for the Members tab), SotdShared.swift (the
#    keychain the app and its widgets share)
#  - SotdWidget: the widgets and the control (io.github.cooperking.sotd.widget)
#  - SotdNotify: the notifications' pictures (io.github.cooperking.sotd.notify)
# Both add-ons are built with the app and put inside it (Embed Foundation Extensions); build.sh signs each with its own
# App Store profile (signing.py).
require 'xcodeproj'

APP_BUNDLE = 'io.github.cooperking.sotd'
proj = Xcodeproj::Project.open('ios/App/App.xcodeproj')
app = proj.targets.find { |t| t.name == 'App' } or abort('No App target in the project')

# 1. the App target's own Swift files
group = proj.main_group.children.find { |g| g.respond_to?(:path) && (g.path == 'App' || g.name == 'App') } or abort('No App group')
%w[SOTDViewController.swift SotdCastPlugin.swift SotdSiri.swift SotdContacts.swift SotdShared.swift].each do |f|
  next if group.files.any? { |r| r.path == f }
  app.add_file_references([group.new_reference(f)])
end

# 2. the add-ons
def add_extension(proj, app, name, bundle, sources)
  return if proj.targets.any? { |t| t.name == name }
  t = proj.new_target(:app_extension, name, :ios, '15.0', nil, :swift)
  g = proj.main_group.new_group(name, name)
  t.add_file_references(sources.map { |f| g.new_reference(f) })
  g.new_reference('Info.plist')
  t.build_configurations.each do |c|
    s = c.build_settings
    s['PRODUCT_BUNDLE_IDENTIFIER'] = bundle
    s['PRODUCT_NAME'] = '$(TARGET_NAME)'
    s['INFOPLIST_FILE'] = "#{name}/Info.plist"
    s['GENERATE_INFOPLIST_FILE'] = 'NO'
    s['SWIFT_VERSION'] = '5.0'
    s['IPHONEOS_DEPLOYMENT_TARGET'] = '15.0'
    s['TARGETED_DEVICE_FAMILY'] = '1,2'
    s['SKIP_INSTALL'] = 'YES'
    s['APPLICATION_EXTENSION_API_ONLY'] = 'YES'
    s['MARKETING_VERSION'] = '1.0'
    s['CURRENT_PROJECT_VERSION'] = '1'
    s['LD_RUNPATH_SEARCH_PATHS'] = ['$(inherited)', '@executable_path/Frameworks', '@executable_path/../../Frameworks']
    s['SWIFT_OPTIMIZATION_LEVEL'] = c.name == 'Debug' ? '-Onone' : '-O'
  end
  app.add_dependency(t)
  phase = app.copy_files_build_phases.find { |p| p.name == 'Embed Foundation Extensions' }
  unless phase
    phase = app.new_copy_files_build_phase('Embed Foundation Extensions')
    phase.symbol_dst_subfolder_spec = :plug_ins
    # straight after Resources, before CocoaPods' script phases, as Xcode itself puts it (so it sees no cycle)
    app.build_phases.delete(phase)
    app.build_phases.insert(app.build_phases.index(app.resources_build_phase) + 1, phase)
  end
  bf = phase.add_file_reference(t.product_reference)
  bf.settings = { 'ATTRIBUTES' => ['RemoveHeadersOnCopy'] }
  puts "added #{name} (#{bundle})"
end

add_extension(proj, app, 'SotdWidget', "#{APP_BUNDLE}.widget", %w[SotdWidget.swift SotdModel.swift SotdViews.swift SotdShared.swift])
add_extension(proj, app, 'SotdNotify', "#{APP_BUNDLE}.notify", %w[NotificationService.swift])
proj.save
puts 'project saved: ' + proj.targets.map(&:name).join(', ')
