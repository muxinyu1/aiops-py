## 设计思想

本项目励志打造为一套代码同时兼容单体版本和微服务版本的项目，
本项目的单体版和微服务版业务功能完全一致，只是实现方式不同，前者使用SpringBoot构建，否则使用SpringCloud构建。
为了使中小型企业在使用本框架时，能自由切换单体版或微服务，这面临一些挑战。

微服务项目涉及到”跨服务“的接口调用，而在单体版中却没有”跨服务“概念，那么，如何实现同一段代码在微服务版本中，能使用openfeign进行跨服务的调用，
单体版中则采取本地接口的方式进行调用呢？

我们只需要在 lamp-xxx-api 模块中定义外观类，在lamp-xxx-boot-impl模块中采用单体版的方式来实现 lamp-xxx-api 中的接口，
在 lamp-xxx-cloud-impl模块中采用微服务的方式来实现 lamp-xxx-api 中的接口，
并且在lamp-boot-server/pom.xml 中只引入lamp-xxx-boot-impl的依赖（不能引入lamp-xxx-cloud-impl）即可，
在lamp-base-server/pom.xml 中只引入lamp-xxx-cloud-impl的依赖（不能引入lamp-xxx-boot-impl）即可。

- lamp-xxx-api
  接口层
- lamp-xxx-boot-impl
  单体版本的具体实现
- lamp-xxx-cloud-impl
  微服务版本的具体实现

lamp-xxx-api 暴露xxx服务的接口，提供给其他服务调用 